"""
excelcat-verify — MCP server for the Verification Layer.

Phase 1 of the MCP refactor (see CLAUDE.md roadmap). This server owns the
on-demand assumption-review computation: it runs RULES_REVIEW + parameter
location over a selection and returns a structured report. It is a pure compute
function — it does NOT emit audit events. The orchestrator (main.py) keeps the
audit chokepoint and wraps every call in a `review_run` event.

Transport: stdio. main.py spawns this as a long-lived subprocess and reuses one
ClientSession across the app lifespan.

The flat `from rules import ...` imports match main.py's import style, so we add
backend/ to sys.path to resolve them regardless of the subprocess cwd.
"""
import os
import sys

# Put backend/ (this file's grandparent) on sys.path so `from rules ...` resolves.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from types import SimpleNamespace
from typing import Any, List, Optional

from mcp.server.fastmcp import FastMCP

from llm_client import _call_model, DEFAULT_MODEL
from rules import RULES, RULES_REVIEW
from rules.base import ReviewContext
from rules.financial.param_locator import locate_all, IS_PERCENTAGE, is_hardcoded
from rules.financial.row_classifier import count_inspectable_cells
from rules.financial.horizontal_formula_consistency import MIN_DATA_CELLS
from rules.financial.hardcode_trend_anomaly import MIN_VALUES

mcp = FastMCP("excelcat-verify")

# Parameter labels surfaced in the review card. Order here is the order the
# located assumptions are reported in.
_REVIEW_PARAM_LABELS = {
    "wacc": "WACC",
    "tgr": "TGR",
    "tax": "Tax rate",
    "beta": "Beta",
    "debt_weight": "% Debt",
    "equity_weight": "% Equity",
}


def _fmt_review_pct(v: float) -> str:
    """Format a decimal-scale value as a percentage, trimming trailing zeros."""
    s = f"{v * 100:.2f}".rstrip("0").rstrip(".")
    return f"{s}%"


def _fmt_review_value(param_key: str, v: float) -> str:
    """Format a located value for display — percentage or plain decimal."""
    if IS_PERCENTAGE.get(param_key, True):
        return _fmt_review_pct(v)
    return f"{v:.2f}"


@mcp.tool()
def review_assumptions(
    values: List[List[Any]],
    formulas: List[List[Any]],
    address: str,
) -> dict:
    """Run RULES_REVIEW + parameter location over a selection. Read-only.

    Returns {located, inspected_cells, results, summary}. Mirrors exactly the
    computation that previously lived inline in main.py's /review endpoint.
    """
    review_ctx = ReviewContext(values=values, formulas=formulas, address=address)

    located = {}
    for key, label in _REVIEW_PARAM_LABELS.items():
        found = locate_all(key, values, address)
        if found:
            located[label] = [
                {
                    "value": _fmt_review_value(key, p.value),
                    "cell": p.cell,
                    # Provenance: True = typed constant, False = formula-driven,
                    # None = couldn't tell (frontend renders a marker only for
                    # definite answers).
                    "hardcoded": is_hardcoded(formulas, p),
                }
                for p in found
            ]

    # Proof-of-work for the row-level rules: count how many data cells of each
    # kind actually cleared the inspection bar. Counting cells (not rows) lets
    # a mixed row's formula and hardcode segments both show up. Without this, a
    # clean trend row looks identical to "we didn't even look" — same ambiguity
    # v1 hit with param locator.
    inspected_cells = count_inspectable_cells(formulas, MIN_DATA_CELLS, MIN_VALUES)

    results = []
    for rule in RULES_REVIEW:
        results.extend(rule.check(review_ctx))

    # Sort warnings before suggestions, with rule_id as a stable tiebreak so
    # the frontend doesn't need its own sort. Same finding always renders in
    # the same place across runs — important for audit-trail consistency too.
    _LEVEL_ORDER = {"warning": 0, "suggestion": 1}
    results.sort(key=lambda r: (_LEVEL_ORDER.get(r.level, 99), r.rule_id))

    warnings = [r for r in results if r.level == "warning"]
    suggestions = [r for r in results if r.level == "suggestion"]

    def _fmt_located_group(label, entries):
        vals = ", ".join(
            f"{e['value']} at {e['cell']}" if e.get("cell") else e["value"]
            for e in entries
        )
        return f"{label} ({vals})"

    def _fmt_cell_inspection() -> str:
        parts = []
        for kind in ("formula", "hardcode"):
            n = inspected_cells[kind]
            if n:
                parts.append(f"{n} {kind} cell{'s' if n != 1 else ''}")
        return ", ".join(parts)

    checked_parts = [_fmt_located_group(k, v) for k, v in located.items()]
    row_summary = _fmt_cell_inspection()
    if row_summary:
        checked_parts.append(f"scanned {row_summary}")
    inspected_anything = bool(located) or bool(row_summary)

    if not inspected_anything:
        summary = "Nothing reviewable in this selection (no assumptions, no data rows)."
    elif not results:
        summary = f"Checked {', '.join(checked_parts)}. No issues found."
    else:
        parts = []
        if warnings:
            parts.append(f"{len(warnings)} warning{'s' if len(warnings) != 1 else ''}")
        if suggestions:
            parts.append(f"{len(suggestions)} suggestion{'s' if len(suggestions) != 1 else ''}")
        summary = f"Review complete: {', '.join(parts)}."

    return {
        "located": located,
        "inspected_cells": inspected_cells,
        "results": [
            {"rule_id": r.rule_id, "level": r.level, "message": r.message}
            for r in results
        ],
        "summary": summary,
    }


@mcp.tool()
def check_rules(tool_call: dict, context: Optional[dict] = None) -> dict:
    """Run the deterministic data-integrity RULES for one action tool_call.

    Pure compute, no audit. Returns {warnings, suggestions, checks_run} with the
    exact shapes pre_write_hook builds inline today. The RULES are duck-typed —
    they only read context.values / .formulas / .address via getattr — so we
    rebuild a lightweight namespace from the 3-field context dict. `context` is
    None when there was no Excel selection (rules then return nothing).
    """
    ctx = None
    if context:
        ctx = SimpleNamespace(
            values=context.get("values", []),
            formulas=context.get("formulas", []),
            address=context.get("address", ""),
        )

    warnings: List[str] = []
    suggestions: List[dict] = []
    checks_run: List[str] = []

    name = tool_call.get("name", "")
    for rule in RULES:
        if rule.applies_to(name):
            # Record every applicable rule, not just ones that yield results —
            # otherwise "checked, clean" and "never evaluated" look identical
            # in the audit trail, which breaks the repeatability claim.
            checks_run.append(rule.id)
            for r in rule.check(tool_call, ctx):
                if r.rule_id != rule.id:
                    checks_run.append(r.rule_id)
                if r.level == "warning":
                    warnings.append(r.message)
                elif r.level == "suggestion":
                    suggestions.append({
                        "field": "value",
                        "original": tool_call.get("args", {}).get("value", ""),
                        "suggested": "",
                        "reason": r.message,
                    })

    # Dedupe (order-preserving): a rule firing multiple results, or emitting a
    # proof-of-work info result, would otherwise list its id twice.
    checks_run = list(dict.fromkeys(checks_run))

    return {"warnings": warnings, "suggestions": suggestions, "checks_run": checks_run}


@mcp.tool()
async def verify_formula(
    original_reply: str,
    context_str: str,
    max_iterations: int = 3,
    selected_model: str = DEFAULT_MODEL,
) -> dict:
    """Reflexion loop: critique → revise → re-verify (up to max_iterations).

    Calls the LLM (via llm_client) to audit Excel formulas in `original_reply`
    against the data state described in `context_str`. Pure compute + LLM, NO
    audit — the orchestrator (main.py) emits the single `reflexion_run` event.
    Returns {final_reply, verified, iterations, log}. No print() — stdout is the
    stdio JSON-RPC channel and must not be written to.
    """
    current_reply = original_reply
    log = []

    for i in range(1, max_iterations + 1):
        # Step 1: Critique
        critique_prompt = f"""You are a strict Excel formula auditor.
        Review the following response and check every formula for correctness
        AGAINST THE USER'S ACTUAL DATA STATE.

        Response to review: {current_reply}
        User's data context: {context_str}

        Rules:
        - Treat the "Data state observations" as ground truth. Do not assume
          data exists if the observations say the selection is empty or
          partially empty.
        - If the formula references a range that is EMPTY according to the
          observations, that IS an error — describe it (e.g. "SUM applied to
          an empty range will return 0 and is likely unintended").
        - If the formula uses a numeric function (SUM, AVERAGE, etc.) on a
          range with NO numeric values, that IS an error.
        - If ALL formulas pass these checks, reply with exactly: "✓ Verified"
        - If ANY formula has an error, describe the error concisely and
          nothing else. Do not rewrite the formula here — just describe what
          is wrong."""
        critique = (await _call_model(selected_model, critique_prompt))["text"].strip()

        # Verified — stop loop early
        if critique == "✓ Verified":
            log.append({"iteration": i, "critique": "✓ Verified", "revised": False})
            return {
                "final_reply": current_reply,
                "verified": True,
                "iterations": i,
                "log": log,
            }

        # Step 2: Revise (only if not last iteration)
        if i < max_iterations:
            revise_prompt = f"""You are an Excel formula expert.
            The following response contains formula errors. Fix ONLY the formulas — keep all other text exactly the same.
            Original response: {current_reply}
            Error identified: {critique}
            User's data context: {context_str}
            Return the full corrected response. Do not add any explanation or preamble."""
            revised_reply = (await _call_model(selected_model, revise_prompt))["text"].strip()
            log.append({"iteration": i, "critique": critique, "revised": True})
            current_reply = revised_reply
        else:
            # Last iteration, still not verified
            log.append({"iteration": i, "critique": critique, "revised": False})

    # Max iterations reached without verification
    return {
        "final_reply": current_reply,
        "verified": False,
        "iterations": max_iterations,
        "log": log,
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
