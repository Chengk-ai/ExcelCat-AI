"""
excelcat-analysis — MCP server for generative analysis capabilities.

The first capability built on the MCP foundation rather than inline in main.py:
year-over-year variance analysis. It runs a deterministic delta computation
(variance.compute_variance) sandwiched between two LLM passes:

  Pass A — structure recognition: locate which rows are line items and which two
           columns are the current/prior year. The LLM returns ONLY structure;
           it never reports a figure.
  compute_variance — pure Python: every delta is arithmetic on the real grid.
  Pass B — interpretation: flag material anomalies + write "Questions for CFO",
           guided by the variance contract injected from excelcat-skills.

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

import json
from typing import Any, List, Optional

from mcp.server.fastmcp import FastMCP

from llm_client import _call_model
from variance import compute_variance

mcp = FastMCP("excelcat-analysis")


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


def _result(
    summary: str,
    *,
    current_label: str = "",
    prior_label: str = "",
    clearly_trivial: float = 0.0,
    variance_table: Optional[list] = None,
    skipped: Optional[list] = None,
    anomalies: Optional[list] = None,
    cfo_questions: Optional[list] = None,
) -> dict:
    """The single return shape for analyse_variance. Every exit path builds its
    result here so the keys can't drift between the empty/degraded/full paths."""
    return {
        "current_label": current_label,
        "prior_label": prior_label,
        "clearly_trivial": clearly_trivial,
        "variance_table": variance_table or [],
        "skipped": skipped or [],
        "anomalies": anomalies or [],
        "cfo_questions": cfo_questions or [],
        "summary": summary,
    }


@mcp.tool()
async def analyse_variance(
    statement: dict,
    contract_md: str = "",
    model: str = "deepseek-v4-flash",
    clearly_trivial: float = 0.0,
) -> dict:
    """Year-over-year variance analysis of one financial statement (v1: Income Statement).

    `statement` = {values, address, sheet} (the worksheet used range; size-capped
    by the orchestrator before it gets here). `contract_md` = the
    variance_analysis.md contract, injected by main.py from excelcat-skills.
    `clearly_trivial` = absolute materiality threshold; line items whose change
    is below it are split out deterministically and never reach the anomaly
    pass. Returns {current_label, prior_label, clearly_trivial, variance_table,
    skipped, anomalies, cfo_questions, summary}. Pure compute + LLM, NO audit,
    no print().
    """
    values: List[List[Any]] = statement.get("values") or []
    address: str = statement.get("address", "")
    try:
        threshold = float(clearly_trivial or 0.0)
    except (TypeError, ValueError):
        threshold = 0.0

    if not values:
        return _result("The statement appears to be empty.", clearly_trivial=threshold)

    # ── Pass A — structure recognition (LLM returns structure only, no figures) ──
    grid_json = json.dumps(values, default=str)
    pass_a_prompt = f"""You are a financial-statement structure parser. You are given the
used range of an Income Statement worksheet as a 2D array (row-major, 0-based indices).

Identify:
- prior_col: the 0-based COLUMN index holding the PRIOR (earlier) year's figures
- current_col: the 0-based COLUMN index holding the CURRENT (most recent) year's figures
- prior_label / current_label: the period labels for those columns (e.g. "FY2024")
- line_items: the rows that are actual income-statement line items (Revenue, COGS,
  Gross profit, each operating expense such as Advertising/Marketing/SG&A, Operating
  income, Net income, etc.). For each, give its label text and its 0-based row index.

Rules:
- Use ONLY the two year columns. Ignore %-change columns, variance columns, and notes.
- Do NOT include blank rows, section headers without figures, or unit/currency rows.
- Return STRICT JSON only, no prose, exactly this shape:
{{"current_col": <int>, "prior_col": <int>, "current_label": "<str>", "prior_label": "<str>",
  "line_items": [{{"label": "<str>", "row": <int>}}]}}

Worksheet address: {address}
Grid (2D array):
{grid_json}"""

    raw_a = (await _call_model(model, pass_a_prompt))["text"]
    mapping = _parse_json(raw_a)
    if not mapping or not mapping.get("line_items"):
        return _result(
            "Could not interpret the statement layout — check that the sheet has "
            "labelled line items and two year columns.",
            clearly_trivial=threshold,
        )

    # ── Deterministic compute (every figure is arithmetic on the real grid) ──
    computed = compute_variance(mapping, values, threshold)
    table = computed.get("rows", [])
    cur_label = computed.get("current_label", "")
    pri_label = computed.get("prior_label", "")
    if not table:
        return _result(
            "No comparable line items with numeric values in both years.",
            current_label=cur_label, prior_label=pri_label,
            clearly_trivial=threshold, skipped=computed.get("skipped", []),
        )

    # Materiality split is deterministic (done in compute_variance). The anomaly
    # pass only ever sees material movements; trivial rows stay in the returned
    # table (flagged) for audit transparency but are kept out of the prompt.
    material = [r for r in table if not r.get("trivial")]
    n_trivial = len(table) - len(material)

    if not material:
        return _result(
            f"All movements are below the clearly-trivial threshold of {threshold:g} — nothing material to flag.",
            current_label=cur_label, prior_label=pri_label,
            clearly_trivial=threshold, variance_table=table,
            skipped=computed.get("skipped", []),
        )

    trivial_note = (
        f"\n{n_trivial} line item(s) whose absolute change was below the clearly-trivial "
        f"threshold of {threshold:g} have been excluded as immaterial — do not mention them."
        if n_trivial else ""
    )

    # ── Pass B — interpretation over the MATERIAL figures (never recomputes) ──
    pass_b_prompt = f"""You are a financial analyst performing year-over-year variance
analysis. Follow the analysis contract below exactly — it defines which cross-line
relationships you must check and how to phrase findings.

── ANALYSIS CONTRACT ──
{contract_md}

── COMPUTED VARIANCE (authoritative — do NOT recompute or restate the numbers) ──
Current period: {cur_label or 'current'}; prior period: {pri_label or 'prior'}.
{_fmt_variance_table(material)}{trivial_note}

Using ONLY these figures, identify material anomalies — especially where two related
line items move in directions that do not make business sense together — then write a
"Questions for CFO" list. If there are no material anomalies, return an empty list and
say so in the summary.

Return STRICT JSON only, exactly this shape:
{{"summary": "<one sentence>",
  "anomalies": [{{"title": "<short>", "detail": "<1-2 sentences>", "lines": ["<line item>"]}}],
  "cfo_questions": ["<question>"]}}"""

    raw_b = (await _call_model(model, pass_b_prompt))["text"]
    analysis = _parse_json(raw_b) or {}

    return _result(
        analysis.get("summary", "") or "",
        current_label=cur_label, prior_label=pri_label,
        clearly_trivial=threshold, variance_table=table,
        skipped=computed.get("skipped", []),
        anomalies=analysis.get("anomalies", []) or [],
        cfo_questions=analysis.get("cfo_questions", []) or [],
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
