"""
excelcat-analysis — MCP server for generative analysis capabilities.

The first capability built on the MCP foundation rather than inline in main.py:
year-over-year variance analysis. Phase 2 extends it from one statement (IS) to
one-or-two statements (IS and/or BS), always as the same sandwich:

  Pass A — structure recognition, once per statement: locate which rows are
           line items (now tagged with a semantic ROLE from a fixed vocabulary)
           and which two columns are the current/prior year. The LLM returns
           ONLY structure; it never reports a figure.
  deterministic layer — pure Python on the real grid values:
           variance.compute_variance  (per statement: every YoY delta)
           ties.check_ties            (BS present: accounting-identity proofs)
           ratios.compute_ratios      (both present: cross-statement ratios)
  Pass B — interpretation over ALL the computed facts: anomalies + "Questions
           for CFO", guided by the variance contract injected from
           excelcat-skills. It interprets figures; it never produces one.

Like excelcat-verify's verify_formula, the LLM passes run here in the server via
the shared llm_client transport. This server is pure compute + LLM: it does NOT
emit audit events and does NOT manage secrets — the orchestrator (main.py) owns
the audit chokepoint and wraps each call in a `variance_run` event.

Transport: stdio. No print() — stdout is the JSON-RPC channel.

The flat imports (`from llm_client ...`, `from variance ...`) match main.py's
style, so we add backend/ to sys.path to resolve them regardless of the
subprocess cwd — same pattern as verify_server.py.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import json
from typing import Any, List, Optional

from mcp.server.fastmcp import FastMCP

from llm_client import _call_model, DEFAULT_MODEL
from variance import compute_variance
from ties import check_ties
from ratios import compute_ratios

mcp = FastMCP("excelcat-analysis")


# Role vocabularies for Pass A. Deliberately tight: only roles that feed the
# deterministic layer (tie-out checks, ratios) or the contract's relationship
# pairs get a name — everything else is "other". A loose vocabulary would
# invite the LLM to freestyle labels the compute layer doesn't understand.
IS_ROLES = (
    "revenue, cogs, gross_profit, marketing, payroll, opex, "
    "depreciation_amortisation, interest_expense, tax, pretax_profit, "
    "operating_profit, net_income, dividends, other"
)
BS_ROLES = (
    "total_assets, receivables, inventory, ppe, cash, payables, debt, "
    "total_liabilities, total_equity, total_liabilities_and_equity, "
    "retained_earnings, dividends, other"
)

_STATEMENT_KINDS = {
    "IS": {
        "name": "Income Statement",
        "roles": IS_ROLES,
        "items_hint": (
            "actual income-statement line items (Revenue, COGS, Gross profit, each "
            "operating expense such as Advertising/Marketing/SG&A, Operating income, "
            "Net income, etc.)"
        ),
    },
    "BS": {
        "name": "Balance Sheet",
        "roles": BS_ROLES,
        "items_hint": (
            "actual balance-sheet line items (asset lines such as Cash, Receivables, "
            "Inventory, PP&E; liability lines such as Payables, Debt; equity lines such "
            "as Retained earnings; and the totals rows — Total assets, Total "
            "liabilities, Total equity, or a combined Total liabilities and equity)"
        ),
    },
}


def _parse_json(text: str) -> Optional[dict]:
    """Pull the first JSON object out of an LLM reply.

    Models occasionally wrap JSON in ```json fences or add a stray sentence, so
    we strip fences and fall back to the outermost {...} span. Returns None if
    nothing parses — callers degrade gracefully rather than trusting junk.
    """
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        # drop the opening fence (``` or ```json) and any trailing fence
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(s[start:end + 1])
    except json.JSONDecodeError:
        return None


async def _locate_structure(role: str, values: list, address: str, model: str) -> Optional[dict]:
    """Pass A for one statement: return the structure mapping, or None."""
    kind = _STATEMENT_KINDS[role]
    grid_json = json.dumps(values, default=str)
    prompt = f"""You are a financial-statement structure parser. You are given the
used range of a {kind['name']} worksheet as a 2D array (row-major, 0-based indices).

Identify:
- prior_col: the 0-based COLUMN index holding the PRIOR (earlier) year's figures
- current_col: the 0-based COLUMN index holding the CURRENT (most recent) year's figures
- prior_label / current_label: the period labels for those columns (e.g. "FY2024")
- line_items: the rows that are {kind['items_hint']}. For each, give its label text,
  its 0-based row index, and its semantic role.

Roles: assign each line item exactly one role from this list (use "other" when
none fits — do NOT invent new role names):
{kind['roles']}

Rules:
- Use ONLY the two year columns. Ignore %-change columns, variance columns, and notes.
- Do NOT include blank rows, section headers without figures, or unit/currency rows.
- Return STRICT JSON only, no prose, exactly this shape:
{{"current_col": <int>, "prior_col": <int>, "current_label": "<str>", "prior_label": "<str>",
  "line_items": [{{"label": "<str>", "row": <int>, "role": "<str>"}}]}}

Worksheet address: {address}
Grid (2D array):
{grid_json}"""
    raw = (await _call_model(model, prompt))["text"]
    mapping = _parse_json(raw)
    if not mapping or not mapping.get("line_items"):
        return None
    return mapping


def _fmt_variance_table(rows: list) -> str:
    """Render variance rows as plain text for Pass B's prompt."""
    lines = []
    for r in rows:
        pct = "n/a (no prior base)" if r["pct_delta"] is None else f"{r['pct_delta'] * 100:+.1f}%"
        lines.append(
            f"- {r['label']}: prior {r['prior']:g} -> current {r['current']:g} "
            f"({r['abs_delta']:+g}, {pct})"
        )
    return "\n".join(lines) if lines else "(no material line items)"


def _fmt_ratio_value(v: Optional[float], unit: str) -> str:
    if v is None:
        return "n/a"
    return f"{v * 100:.1f}%" if unit == "pct" else f"{v:.1f} days"


def _fmt_checks(checks: list) -> str:
    lines = [
        f"- [{c['status'].upper()}] {c['label']}: {c['detail']}"
        for c in checks
    ]
    return "\n".join(lines) if lines else "(no checks ran)"


def _fmt_ratios(ratios: list) -> str:
    lines = []
    for r in ratios:
        if r["status"] != "ok":
            lines.append(f"- [SKIPPED] {r['label']}: {r.get('reason', '')}")
            continue
        unit = r["unit"]
        delta = r.get("delta")
        delta_txt = ""
        if delta is not None:
            delta_txt = f" ({'+' if delta >= 0 else ''}{delta * 100:.1f}pp)" if unit == "pct" \
                else f" ({delta:+.1f} days)"
        lines.append(
            f"- {r['label']}: prior {_fmt_ratio_value(r.get('prior'), unit)} -> "
            f"current {_fmt_ratio_value(r.get('current'), unit)}{delta_txt} [{r['basis']}]"
        )
    return "\n".join(lines) if lines else "(no ratios computed)"


def _result(
    summary: str,
    *,
    clearly_trivial: float = 0.0,
    statements: Optional[list] = None,
    checks: Optional[list] = None,
    ratios: Optional[list] = None,
    anomalies: Optional[list] = None,
    cfo_questions: Optional[list] = None,
) -> dict:
    """The single return shape for analyse_variance. Every exit path builds its
    result here so the keys can't drift between the empty/degraded/full paths."""
    return {
        "clearly_trivial": clearly_trivial,
        "statements": statements or [],
        "checks": checks or [],
        "ratios": ratios or [],
        "anomalies": anomalies or [],
        "cfo_questions": cfo_questions or [],
        "summary": summary,
    }


def _statement_entry(role: str, stmt: dict, computed: Optional[dict] = None,
                     error: str = "") -> dict:
    """One entry of the result's `statements` list. `computed` is a
    compute_variance result; `error` marks a degraded statement (Pass A failed,
    empty sheet) — the entry still appears so the UI can say what happened."""
    entry = {
        "role": role,
        "sheet": stmt.get("sheet", ""),
        "address": stmt.get("address", ""),
        "current_label": (computed or {}).get("current_label", ""),
        "prior_label": (computed or {}).get("prior_label", ""),
        "current_col": (computed or {}).get("current_col"),
        "prior_col": (computed or {}).get("prior_col"),
        "variance_table": (computed or {}).get("rows", []),
        "skipped": (computed or {}).get("skipped", []),
    }
    if error:
        entry["error"] = error
    return entry


@mcp.tool()
async def analyse_variance(
    statements: List[dict],
    contract_md: str = "",
    model: str = DEFAULT_MODEL,
    clearly_trivial: float = 0.0,
) -> dict:
    """Year-over-year variance analysis over one or two financial statements.

    `statements` = [{role: "IS"|"BS", values, address, sheet}, ...] (worksheet
    used ranges; size-capped by the orchestrator before they get here). One
    statement → single-statement analysis; IS + BS → adds tie-out checks and
    the deterministic cross-statement ratio layer. `contract_md` = the
    variance_analysis.md contract, injected by main.py from excelcat-skills.
    `clearly_trivial` = absolute materiality threshold, shared across both
    statements; it is also the tolerance for the tie-out checks. Returns
    {clearly_trivial, statements, checks, ratios, anomalies, cfo_questions,
    summary}. Pure compute + LLM, NO audit, no print().
    """
    try:
        threshold = float(clearly_trivial or 0.0)
    except (TypeError, ValueError):
        threshold = 0.0

    # First statement per role wins; unknown roles are ignored (the orchestrator
    # validates, this is belt-and-braces).
    by_role: dict = {}
    for stmt in statements or []:
        role = str((stmt or {}).get("role", "")).upper()
        if role in _STATEMENT_KINDS and role not in by_role:
            by_role[role] = stmt

    if not by_role:
        return _result("No statement supplied.", clearly_trivial=threshold)

    # ── Pass A per statement, concurrently (structure only, no figures) ──
    roles_order = [r for r in ("IS", "BS") if r in by_role]
    locates = await asyncio.gather(*[
        _locate_structure(
            r,
            by_role[r].get("values") or [],
            by_role[r].get("address", ""),
            model,
        ) if by_role[r].get("values") else _noop()
        for r in roles_order
    ])
    mappings = dict(zip(roles_order, locates))

    # ── Deterministic layer (every figure is arithmetic on the real grids) ──
    entries: list = []
    computed: dict = {}
    for role in roles_order:
        stmt = by_role[role]
        if not stmt.get("values"):
            entries.append(_statement_entry(role, stmt, error="The sheet appears to be empty."))
            continue
        mapping = mappings[role]
        if not mapping:
            entries.append(_statement_entry(
                role, stmt,
                error="Could not interpret the statement layout — check that the sheet "
                      "has labelled line items and two year columns.",
            ))
            continue
        comp = compute_variance(mapping, stmt["values"], threshold)
        computed[role] = (mapping, comp)
        entries.append(_statement_entry(role, stmt, computed=comp))

    is_ok = "IS" in computed
    bs_ok = "BS" in computed

    checks: list = []
    if bs_ok:
        checks = check_ties(
            computed["BS"][0], by_role["BS"]["values"], threshold,
            is_mapping=computed["IS"][0] if is_ok else None,
            is_grid=by_role["IS"]["values"] if is_ok else None,
        )

    ratios: list = []
    if is_ok and bs_ok:
        ratios = compute_ratios(
            computed["IS"][0], by_role["IS"]["values"],
            computed["BS"][0], by_role["BS"]["values"],
        )

    all_rows = [r for _, comp in computed.values() for r in comp.get("rows", [])]
    if not all_rows:
        return _result(
            "No comparable line items with numeric values in both years.",
            clearly_trivial=threshold, statements=entries,
            checks=checks, ratios=ratios,
        )

    # Materiality split is deterministic (done in compute_variance). The anomaly
    # pass only ever sees material movements; trivial rows stay in the returned
    # tables (flagged) for audit transparency but are kept out of the prompt.
    material_by_role = {
        role: [r for r in comp.get("rows", []) if not r.get("trivial")]
        for role, (_, comp) in computed.items()
    }
    n_material = sum(len(v) for v in material_by_role.values())
    n_trivial = len(all_rows) - n_material

    if n_material == 0:
        return _result(
            f"All movements are below the clearly-trivial threshold of {threshold:g} — nothing material to flag.",
            clearly_trivial=threshold, statements=entries,
            checks=checks, ratios=ratios,
        )

    trivial_note = (
        f"\n{n_trivial} line item(s) whose absolute change was below the clearly-trivial "
        f"threshold of {threshold:g} have been excluded as immaterial — do not mention them."
        if n_trivial else ""
    )

    # ── Pass B — interpretation over ALL the computed facts (never recomputes) ──
    stmt_sections = []
    for role in roles_order:
        if role not in computed:
            continue
        _, comp = computed[role]
        stmt_sections.append(
            f"{_STATEMENT_KINDS[role]['name'].upper()} "
            f"(current: {comp.get('current_label') or 'current'}; "
            f"prior: {comp.get('prior_label') or 'prior'}):\n"
            f"{_fmt_variance_table(material_by_role[role])}"
        )
    facts_sections = "\n\n".join(stmt_sections)

    checks_section = (
        f"\n\n── TIE-OUT CHECKS (deterministic arithmetic — treat as established facts) ──\n"
        f"{_fmt_checks(checks)}"
        if checks else ""
    )
    ratios_section = (
        f"\n\n── CROSS-STATEMENT RATIOS (computed for you — NEVER compute a ratio yourself) ──\n"
        f"{_fmt_ratios(ratios)}"
        if ratios else ""
    )

    pass_b_prompt = f"""You are a financial analyst performing year-over-year variance
analysis. Follow the analysis contract below exactly — it defines which cross-line
relationships you must check and how to phrase findings.

── ANALYSIS CONTRACT ──
{contract_md}

── COMPUTED VARIANCE (authoritative — do NOT recompute or restate the numbers) ──
{facts_sections}{trivial_note}{checks_section}{ratios_section}

Using ONLY these figures, identify material anomalies — especially where two related
line items move in directions that do not make business sense together — then write a
"Questions for CFO" list. A FAILED or INFO tie-out check is itself anomaly material:
reference it directly rather than rediscovering it. If there are no material
anomalies, return an empty list and say so in the summary.

Return STRICT JSON only, exactly this shape:
{{"summary": "<one sentence>",
  "anomalies": [{{"title": "<short>", "detail": "<1-2 sentences>", "lines": ["<line item>"]}}],
  "cfo_questions": ["<question>"]}}"""

    raw_b = (await _call_model(model, pass_b_prompt))["text"]
    analysis = _parse_json(raw_b) or {}

    return _result(
        analysis.get("summary", "") or "",
        clearly_trivial=threshold, statements=entries,
        checks=checks, ratios=ratios,
        anomalies=analysis.get("anomalies", []) or [],
        cfo_questions=analysis.get("cfo_questions", []) or [],
    )


async def _noop() -> None:
    """Placeholder awaitable for statements with no values (keeps gather zip-aligned)."""
    return None


if __name__ == "__main__":
    mcp.run(transport="stdio")
