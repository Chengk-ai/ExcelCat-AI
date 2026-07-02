from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from typing import List, Optional, Any, Literal
import os
import sys
import re
import json
import uuid
from contextlib import asynccontextmanager, AsyncExitStack
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import audit
import audit_render
# LLM transport + tool registry now live in llm_client (shared with the
# excelcat-verify MCP server, which runs the reflexion loop). main.py only needs
# _call_model for the /chat passes and FORECAST_ONLY_TOOLS for the forecast pass.
from llm_client import _call_model, FORECAST_ONLY_TOOLS

# ── MCP servers ───────────────────────────────────────────
# The MCP refactor splits backend-owned logic into stdio MCP servers, spawned
# once and held open for the app's lifetime. main.py stays the orchestrator and
# keeps the audit chokepoint — servers never touch audit.
#   - excelcat-verify   : review_assumptions, check_rules, verify_formula
#   - excelcat-skills   : get_skill, list_skills (the skill contracts)
#   - excelcat-analysis : analyse_variance (deterministic compute + 2 LLM passes)
_MCP_SERVERS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_servers")
_VERIFY_SERVER = os.path.join(_MCP_SERVERS_DIR, "verify_server.py")
_SKILLS_SERVER = os.path.join(_MCP_SERVERS_DIR, "skills_server.py")
_ANALYSIS_SERVER = os.path.join(_MCP_SERVERS_DIR, "analysis_server.py")


@asynccontextmanager
async def lifespan(app: FastAPI):
    stack = AsyncExitStack()
    app.state.mcp_verify = None
    app.state.mcp_skills = None
    app.state.mcp_analysis = None
    # Skill-name routing sets, cached from the skills server at startup so the
    # /chat hot path doesn't round-trip per request (and so the registry has a
    # single source of truth — no copy in main.py to drift).
    app.state.skill_text_names = set()
    app.state.skill_action_names = set()

    async def _connect(server_path: str, label: str):
        # One server crashing must not take the API (or the other server) down;
        # each capability degrades on its own (see endpoints for fail-loud paths).
        params = StdioServerParameters(command=sys.executable, args=[server_path])
        read, write = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        print(f"[MCP] {label} session initialised")
        return session

    try:
        app.state.mcp_verify = await _connect(_VERIFY_SERVER, "excelcat-verify")
    except Exception as e:
        print(f"[MCP] failed to start excelcat-verify: {e}")

    try:
        app.state.mcp_skills = await _connect(_SKILLS_SERVER, "excelcat-skills")
        listed = _unwrap_tool_result(await app.state.mcp_skills.call_tool("list_skills", {}))
        app.state.skill_text_names = set(listed.get("text", []))
        app.state.skill_action_names = set(listed.get("action", []))
        print(f"[MCP] skills cached: text={sorted(app.state.skill_text_names)} "
              f"action={sorted(app.state.skill_action_names)}")
    except Exception as e:
        print(f"[MCP] failed to start excelcat-skills: {e}")

    try:
        app.state.mcp_analysis = await _connect(_ANALYSIS_SERVER, "excelcat-analysis")
    except Exception as e:
        print(f"[MCP] failed to start excelcat-analysis: {e}")

    try:
        yield
    finally:
        await stack.aclose()
        app.state.mcp_verify = None
        app.state.mcp_skills = None
        app.state.mcp_analysis = None


app = FastAPI(lifespan=lifespan)

# Only the add-in's own origin (webpack dev server, per manifest.xml) may call
# this API. The old wildcard + allow_credentials combo is invalid per the CORS
# spec and needlessly let any local page hit the backend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Skills loader (via excelcat-skills MCP server) ────────────
# The skill registry + markdown contracts now live in mcp_servers/skills_server.py.
# main.py fetches content on demand and fails loud if the service is unavailable —
# it does not silently answer without the skill contract.
async def _load_skill_content(tool_name: str) -> Optional[str]:
    """Fetch a skill's markdown from the excelcat-skills MCP server.

    Returns the content, or None if the tool isn't a skill / its file is missing.
    Raises RuntimeError if the skills service is unavailable (caller surfaces it).
    """
    session = getattr(app.state, "mcp_skills", None)
    if session is None:
        raise RuntimeError("skills service not running")
    res = _unwrap_tool_result(await session.call_tool("get_skill", {"tool_name": tool_name}))
    return res.get("content")


def _skills_unavailable_response(request_id: str, audit_enabled: bool) -> dict:
    """Graceful /chat response when the skills service is down — honest, not silent."""
    if audit_enabled:
        audit.append_event(
            "chat_response",
            {"skill_used": False, "tool_calls_proposed": [], "reply_excerpt": "skills service unavailable", "error": True},
            request_id=request_id,
            enabled=audit_enabled,
        )
    return {
        "reply": "The skills service is unavailable right now — please try again in a moment.",
        "tool_calls": [],
        "skill_used": False,
        "review": None,
        "request_id": request_id,
    }

# ── Function calling + LLM transport moved to llm_client.py ──
# The tool registry (OPENAI_TOOLS / tools / FORECAST_ONLY_TOOLS) and the
# _call_deepseek / _call_model transport now live in llm_client.py, shared
# with the excelcat-verify MCP server. Imported at the top of this file.


# ── Reflexion Pattern (critique → revise → re-verify, up to 3×) ──────────────
def contains_formula(text: str) -> bool: # check if the text contains a formula
    return bool(re.search(r'`?=\s*[A-Z\(]', text, re.IGNORECASE)) # return True if the text contains a formula

def analyse_data_state(context: Optional["SelectionContext"]) -> str:
    """
    Inspect the selection's actual data and return a short, factual summary
    that we can inject into Reflexion's critique prompt.

    Why this exists: Gemini-as-critic does not actually run formulas or
    inspect cell values — it only reads what's in the prompt. If we just
    pass the raw context string, the critic won't notice "the range is
    empty" and will rubber-stamp formulas that would error out in Excel.
    This helper turns the data into explicit facts the critic can't miss.

    Returns:
        A multi-line string of facts, e.g.:
            "- Selection range: A1:A1
             - Selection is EMPTY (no values found)
             - No numeric values present in selection"
    """
    if context is None:
        return "- No Excel selection context was provided."

    facts = [f"- Selection range: {context.address}"]

    # Flatten and inspect the cell values
    flat = [v for row in (context.values or []) for v in row]
    non_empty = [v for v in flat if v not in (None, "", " ")]
    numeric = [v for v in non_empty if isinstance(v, (int, float)) and not isinstance(v, bool)]

    if not flat:
        facts.append("- Selection is EMPTY (no cells in range).")
    elif not non_empty:
        facts.append("- Selection is EMPTY (all cells are blank).")
    elif len(non_empty) < len(flat):
        facts.append(f"- Selection is PARTIALLY EMPTY ({len(flat) - len(non_empty)} of {len(flat)} cells are blank).")
    else:
        facts.append(f"- Selection has {len(non_empty)} non-empty cells.")

    if non_empty and not numeric:
        facts.append("- No numeric values present in selection (functions like SUM/AVERAGE will fail or return 0).")
    elif numeric:
        facts.append(f"- {len(numeric)} numeric values present.")

    return "\n".join(facts)

async def reflexion_review(
    original_reply: str,
    context_str: str,
    max_iterations: int = 3,
    *,
    selected_model: str = "deepseek-v4-flash",
    request_id: Optional[str] = None,
    audit_enabled: bool = True,
) -> dict:
    """
    Thin client over the excelcat-verify MCP server's `verify_formula` tool,
    which now runs the reflexion loop (critique → revise → re-verify, up to 3×).

    The loop moved into the server (Phase 3 of the MCP refactor); this wrapper
    keeps the audit chokepoint in main.py — it emits the single `reflexion_run`
    event after the loop returns, with the full critique chain so an auditor can
    replay every iteration.

    Returns {final_reply, verified, iterations, log}. Fails loud: if the
    verification service is unavailable, the original reply passes through
    UNVERIFIED with the reason recorded in `log` — never silently "verified".
    """
    session = getattr(app.state, "mcp_verify", None)
    if session is None:
        result = {
            "final_reply": original_reply,
            "verified": False,
            "iterations": 0,
            "log": [{"error": "verification service not running"}],
        }
    else:
        try:
            result = _unwrap_tool_result(await session.call_tool(
                "verify_formula",
                {
                    "original_reply": original_reply,
                    "context_str": context_str,
                    "max_iterations": max_iterations,
                    "selected_model": selected_model,
                },
            ))
        except Exception as e:
            result = {
                "final_reply": original_reply,
                "verified": False,
                "iterations": 0,
                "log": [{"error": f"verification service error: {e}"}],
            }

    if request_id:
        audit.append_event(
            "reflexion_run",
            {
                "original": original_reply,
                "final_reply": result["final_reply"],
                "verified": result["verified"],
                "iterations": result["iterations"],
                "log": result["log"],
            },
            request_id=request_id,
            enabled=audit_enabled,
        )
    return result

# ── Pre-write hook (Verification Layer interception point) ──
async def pre_write_hook(
    tool_call: dict,
    context_str: str,
    context: Optional["SelectionContext"] = None,
    *,
    selected_model: str = "deepseek-v4-flash",
    request_id: Optional[str] = None,
    audit_enabled: bool = True,
) -> dict:
    """
    Runs checks on an action tool_call before it's returned to the frontend
    for execution.

    This is the core interception point for the Verification Layer. Currently
    it only runs one check (formula correctness), but more will be added here
    later (debit/credit balance, WACC sanity check, audit log, etc.).

    Note: this hook does NOT block any tool_call — the check results are
    returned as metadata, and the frontend's approval UI decides whether to
    let the user proceed. This design supports the "Ask to edit" experience:
    the user sees the warnings and makes the final call themselves.

    Args:
        tool_call: {"name": str, "args": dict} — e.g. write_to_cell
        context_str: string description of the current Excel selection
        context: the raw SelectionContext, used for data-aware checks
                 (e.g. detecting empty selections that would make SUM fail)

    Returns:
        {
            "status": "ok" | "warning" | "suggestion",  # overall status
            "warnings": list[str],         # hard issues to surface to the user
            "suggestions": list[dict],     # AI alternatives the user can pick
            "checks_run": list[str],       # which checks ran (for audit trail)
            "review_meta": dict | None,    # detailed reflexion log
        }

        Each suggestion is shaped:
        {
            "field": "value",          # which arg the suggestion targets
            "original": "=SUM(B1+B10)",
            "suggested": "=B1+B10",
            "reason": "SUM is redundant — its argument is already a value."
        }
    """
    warnings = []
    checks_run = []
    review_meta = None
    suggestions = []  # AI's alternative versions, surfaced to the user

    # Build a richer context for the critic — raw description + factual
    # observations about the data state. The critic ignores nuance unless
    # we put facts in front of it explicitly.
    data_facts = analyse_data_state(context)
    enriched_context = f"{context_str}\n\nData state observations:\n{data_facts}"

    # ── Check 1: Formula correctness + AI suggestion diff ──
    # Strategy: run reflexion on the original formula. Reflexion may rewrite it
    # internally (to fix errors or "optimise"). We compare the original to what
    # reflexion ended up with. If they differ, we surface the AI's version as a
    # SUGGESTION — not an automatic replacement. The user decides on the
    # approval card whether to keep their original, accept the AI's version,
    # or reject entirely.
    if tool_call["name"] == "write_to_cell":
        original = tool_call.get("args", {}).get("value", "")
        if original and contains_formula(original):
            checks_run.append("formula_correctness")
            print(f"[HOOK] Running formula check on: {original}")
            review_meta = await reflexion_review(
                original,
                enriched_context,
                selected_model=selected_model,
                request_id=request_id,
                audit_enabled=audit_enabled,
            )

            # Reflexion may wrap its output in prose ("Here is the fixed
            # formula: =B1+B10"). Pull out the formula token for a clean diff.
            ai_version = _extract_formula(review_meta["final_reply"])

            if not review_meta["verified"]:
                # Reflexion couldn't even fix it — hard warning.
                warnings.append(
                    f"Formula did not pass verification after "
                    f"{review_meta['iterations']} attempts — please review manually."
                )
            elif ai_version and _normalise_formula(ai_version) != _normalise_formula(original):
                # Verified, but the AI changed something. Surface as a
                # suggestion so the user can compare on the approval card.
                print(f"[HOOK] AI suggests: {ai_version} (original: {original})")
                suggestions.append({
                    "field": "value",
                    "original": original,
                    "suggested": ai_version,
                    "reason": _first_critique(review_meta["log"]),
                })

    # ── Batch pattern: same check, but on the templated pattern instead
    # of N individual formulas. We instantiate a sample by replacing {r}
    # with the first row in the range, then run reflexion once. Result
    # represents the whole batch.
    if tool_call["name"] == "apply_formula_pattern":
        args = tool_call.get("args", {})
        pattern = args.get("pattern", "")
        cells   = args.get("cells", [])
        if pattern and cells and contains_formula(pattern):
            checks_run.append("formula_correctness")
            sample_row = _parse_cell(cells[0])[1] if _parse_cell(cells[0]) else 1
            sample_formula = pattern.replace("{r}", str(sample_row))
            print(f"[HOOK] Running pattern check on: {pattern} (sample: {sample_formula})")
            review_meta = await reflexion_review(
                sample_formula,
                enriched_context,
                selected_model=selected_model,
                request_id=request_id,
                audit_enabled=audit_enabled,
            )

            ai_version = _extract_formula(review_meta["final_reply"])
            if not review_meta["verified"]:
                warnings.append(
                    f"Pattern did not pass verification after "
                    f"{review_meta['iterations']} attempts — please review manually."
                )
            elif ai_version and _normalise_formula(ai_version) != _normalise_formula(sample_formula):
                # Pattern-level suggestion. We don't re-template the AI's
                # version back into a pattern (that would need symbolic
                # diffing); just surface the sample-level diff so the user
                # sees what changed. Batch UI doesn't support "Use AI's
                # version" yet, so this is informational only.
                suggestions.append({
                    "field": "value",
                    "original":  sample_formula,
                    "suggested": ai_version,
                    "reason": _first_critique(review_meta["log"]),
                })

    # ── Forecast batch: one structured action carrying N formulas. Run
    # reflexion on a representative formula (the first) for correctness. The
    # acceptance-range check comes from the deterministic rules below.
    if tool_call["name"] == "apply_forecast":
        args = tool_call.get("args", {})
        values = args.get("values", []) or []
        sample = values[0] if values else ""
        if sample and contains_formula(sample):
            checks_run.append("formula_correctness")
            print(f"[HOOK] Running forecast check on sample: {sample}")
            review_meta = await reflexion_review(
                sample,
                enriched_context,
                selected_model=selected_model,
                request_id=request_id,
                audit_enabled=audit_enabled,
            )
            ai_version = _extract_formula(review_meta["final_reply"])
            if not review_meta["verified"]:
                warnings.append(
                    f"Forecast formula did not pass verification after "
                    f"{review_meta['iterations']} attempts — please review manually."
                )
            elif ai_version and _normalise_formula(ai_version) != _normalise_formula(sample):
                suggestions.append({
                    "field": "value",
                    "original":  sample,
                    "suggested": ai_version,
                    "reason": _first_critique(review_meta["log"]),
                })

    # ── Check 2: Data Integrity (deterministic rules, via excelcat-verify MCP) ──
    # The rules run in the MCP server (single source of truth). This sits on the
    # audit chokepoint, so we fail loud, never silent: if the server is down we
    # surface a warning AND record `rules_unavailable` in checks_run, so the
    # approval card and the audit trail both show that integrity checks were
    # degraded rather than pretending they passed.
    ctx_payload = None
    if context is not None:
        ctx_payload = {
            "values": getattr(context, "values", []),
            "formulas": getattr(context, "formulas", []),
            "address": getattr(context, "address", ""),
        }
    session = getattr(app.state, "mcp_verify", None)
    if session is None:
        warnings.append("Data-integrity rules could not run (verification service not running).")
        checks_run.append("rules_unavailable")
    else:
        try:
            rules_res = _unwrap_tool_result(await session.call_tool(
                "check_rules",
                {"tool_call": tool_call, "context": ctx_payload},
            ))
            warnings.extend(rules_res.get("warnings", []))
            suggestions.extend(rules_res.get("suggestions", []))
            checks_run.extend(rules_res.get("checks_run", []))
        except Exception as e:
            warnings.append(f"Data-integrity rules could not run (verification service error: {e}).")
            checks_run.append("rules_unavailable")

    # ── Check 3: Financial Logic (to be added) ──
    # e.g. is WACC within a reasonable 5%–15% range

    # ── Check 4: Audit Trail ──
    # Reflexion already emitted its own event above (when it ran). Here we
    # add the hook-level summary: tool, status, warnings, suggestions, checks
    # run. This is the "did the Verification Layer flag it?" record.
    status = _hook_status(warnings, suggestions)
    if request_id:
        audit.append_event(
            "hook_check",
            {
                "tool_name": tool_call.get("name", ""),
                "args": tool_call.get("args", {}),
                "status": status,
                "warnings": warnings,
                "suggestions": suggestions,
                "checks_run": checks_run,
            },
            request_id=request_id,
            enabled=audit_enabled,
        )

    return {
        "status": status,
        "warnings": warnings,
        "suggestions": suggestions,
        "checks_run": checks_run,
        "review_meta": review_meta,
    }


def _hook_status(warnings: list, suggestions: list) -> str:
    """Overall hook status. warnings > suggestions > ok."""
    if warnings:
        return "warning"
    if suggestions:
        return "suggestion"
    return "ok"


def _extract_formula(text: str) -> str:
    """
    Reflexion's output may include prose ("Here is the fixed formula:
    `=B1+B10`. This avoids..."). Pull out the first formula-like token.
    Returns the cleaned formula, or empty string if none found.

    Strategy (checked in order):
      1. Backtick-wrapped: `=SUM(A1:A10)` — highest confidence.
      2. Bare formula: =FUNC(...) running until the parentheses balance
         or a sentence-ending character is hit. Handles "Use =A1+B1 instead."
      3. If the entire text looks like a formula (starts with =, no prose),
         return it directly.
      4. Otherwise return "" — caller treats this as "no formula extracted".
         Returning the raw prose would cause false suggestions downstream.
    """
    if not text:
        return ""
    s = text.strip()

    # 1. Backtick-wrapped formula anywhere in the text.
    m = re.search(r'`(=[^`]+)`', s)
    if m:
        return m.group(1).strip()

    # 2. Bare formula: starts with =, greedily consumes formula characters.
    #    Tracks parenthesis depth so =IF(A1>0,"yes","no") isn't truncated
    #    at the comma. Outside parens, stops at the first character that
    #    can't be part of a formula (letters that form prose words, etc.).
    m = re.search(r'=[A-Za-z(]', s)
    if m:
        start = m.start()
        depth = 0
        end = start
        for i in range(start, len(s)):
            ch = s[i]
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth <= 0:
                    end = i + 1
                    break
            elif depth == 0:
                if ch == ' ':
                    rest = s[i:].lstrip()
                    if not rest:
                        end = i
                        break
                    c0 = rest[0]
                    if c0 in '+-*/^&<>=,()':
                        continue
                    if c0 == '$' or c0.isdigit():
                        continue
                    if c0.isalpha() and len(rest) > 1 and (rest[1].isdigit() or rest[1] == '('):
                        continue
                    end = i
                    break
                elif ch in '\n':
                    end = i
                    break
        else:
            end = len(s)
        candidate = s[start:end].strip().rstrip('.,;:!?')
        if candidate and len(candidate) > 1:
            return candidate

    # 3. Whole text is a formula (no prose around it).
    if s.startswith('=') and ' ' not in s[:6]:
        return s

    return ""


def _normalise_formula(formula: str) -> str:
    """
    Normalise a formula for equality comparison: strip backticks, whitespace,
    and uppercase function names so trivial differences don't trigger a false
    suggestion.
    """
    if not formula:
        return ""
    cleaned = formula.strip().strip("`").strip()
    # Collapse internal whitespace
    cleaned = re.sub(r'\s+', '', cleaned)
    return cleaned.upper()


# ── Batch-write collapse (row-only-diff pattern) ─────────────────────────────
# Conservative: only fires when ≥3 consecutive write_to_cell calls target the
# same column on contiguous rows AND every value reduces to the same template
# once row numbers are abstracted away. Any deviation → fallback to N
# individual tool_calls. This keeps the audit story honest: we never group
# things that aren't actually identical patterns.

_CELL_REF_RE = re.compile(r'^([A-Za-z]+)(\d+)$')

def _parse_cell(cell: str):
    """Return (column_letters_upper, row_int) or None if not a plain A1 ref."""
    if not cell:
        return None
    m = _CELL_REF_RE.match(cell.strip())
    if not m:
        return None
    return m.group(1).upper(), int(m.group(2))


def _row_template(value: str, row: int) -> str:
    """
    Replace occurrences of `<col><row>` (where <row> matches the cell's row)
    with `<col>{r}` so two values from different rows of the same pattern
    compare equal. Conservative: only replaces digits that are preceded by
    column letters AND equal to this cell's row. Numeric literals (e.g. 100,
    0.5) are left alone.
    """
    if not value:
        return ""
    # Match anything like B12, AC4 — column letters followed by row digits.
    pattern = re.compile(r'([A-Za-z]+)(\d+)')

    def sub(m):
        col, num = m.group(1), m.group(2)
        if int(num) == row:
            return f"{col.upper()}{{r}}"
        # Different row number — keep as-is so e.g. =A2+$B$10 doesn't
        # falsely collapse with =A3+$B$10.
        return f"{col.upper()}{num}"

    return pattern.sub(sub, value)


def _is_formula_value(value: str) -> bool:
    """A cell write is a formula if its (stripped) value begins with '='."""
    return bool(value) and value.strip().startswith("=")


def _build_pattern_card(col: str, pattern: str, block: list) -> dict:
    """
    Build one apply_formula_pattern card from a block of (row, idx, cell, value)
    tuples (sorted by row; an optional text header may be at the front). `range`
    spans the block's first→last row; the explicit cells/values list is the
    source of truth, so a skipped row (e.g. a subtotal) simply doesn't appear.
    """
    cells = [it[2] for it in block]
    values = [it[3] for it in block]
    rows = [it[0] for it in block]
    rng = f"{col}{min(rows)}:{col}{max(rows)}"
    return {
        "name": "apply_formula_pattern",
        "args": {
            "pattern": pattern,
            "range":   rng,
            "cells":   cells,
            "values":  values,
        },
    }


def _collapse_row_pattern_writes(tool_calls: list[dict]) -> list[dict]:
    """
    Collapse a column's write_to_cell calls into one apply_formula_pattern card
    per formula pattern, so the user confirms a whole column fill once instead
    of N times.

    Order-independent and gap-tolerant. The model interleaves columns
    (D2, E2, D3, E3, …) and skips non-data rows (subtotals, blank separators),
    so we bucket by column and then group each column's formula cells by their
    row-template — NOT by contiguous rows. A skipped subtotal row must not split
    one column into two cards. A single text header rides along when it sits
    directly above a group's top cell (e.g. "Year Diff" above =C{r}-B{r}).

    Honesty bar: we only group same-column cells whose formulas reduce to one
    identical pattern; every cell→value (header included) is listed explicitly
    on the card, gaps and all. Non-write tools, unparseable writes, and groups
    under the ≥3 bar are preserved as individual items, ordered by original
    position so the approval-card order stays stable.
    """
    if not tool_calls:
        return tool_calls

    # Bucket parseable cell writes by column; keep everything else as
    # passthrough, each tagged with its original index so we can restore order.
    columns: dict = {}                 # col -> [(row, idx, cell, value), …]
    passthrough: list = []             # [(idx, tool), …]
    for idx, t in enumerate(tool_calls):
        parsed = None
        value = ""
        if t.get("name") == "write_to_cell":
            parsed = _parse_cell(t.get("args", {}).get("cell", ""))
            value = t.get("args", {}).get("value", "")
        if parsed and value != "":
            col, row = parsed
            columns.setdefault(col, []).append((row, idx, t["args"]["cell"], value))
        else:
            passthrough.append((idx, t))

    emitted: list = list(passthrough)  # [(order_idx, tool), …]

    def _emit_individual(items):
        for _row, idx, _cell, _value in items:
            emitted.append((idx, tool_calls[idx]))

    for col, items in columns.items():
        items.sort(key=lambda x: x[0])  # by row
        formula_items = [it for it in items if _is_formula_value(it[3])]
        header_items  = [it for it in items if not _is_formula_value(it[3])]

        # Group formula cells by row-template (gaps allowed). A column with a
        # single uniform fill yields one group; a genuinely different formula
        # (e.g. a subtotal row) lands in its own group and is judged separately.
        groups: dict = {}              # template -> [items], row-sorted
        for it in formula_items:
            groups.setdefault(_row_template(it[3], it[0]), []).append(it)

        # A lone text header attaches to the group it sits directly above; with
        # 0 or 2+ headers we don't guess — they go through as individual writes.
        header = header_items[0] if len(header_items) == 1 else None
        header_used = False

        for pattern, group in groups.items():
            if len(group) < 3:
                _emit_individual(group)
                continue
            block = group
            if header and not header_used and header[0] == group[0][0] - 1:
                block = [header] + group
                header_used = True
            card = _build_pattern_card(col, pattern, block)
            emitted.append((min(it[1] for it in block), card))

        # Header that never attached (or 2+ ambiguous headers) → individual.
        if header and not header_used:
            _emit_individual([header])
        if len(header_items) > 1:
            _emit_individual(header_items)

    emitted.sort(key=lambda x: x[0])
    return [tool for _, tool in emitted]


def _first_critique(log: list) -> str:
    """Pull the first non-verified critique from a reflexion log, for UX."""
    for entry in log:
        critique = entry.get("critique", "")
        if critique and critique != "✓ Verified":
            return critique
    return ""

# ── Models ────────────────────────────────────────────────
class SelectionContext(BaseModel):
    address: str
    sheet: str
    values: List[List[Any]]
    formulas: List[List[Any]] = []
    rowCount: int
    columnCount: int

class ChatRequest(BaseModel):
    message: str
    context: Optional[SelectionContext] = None
    model: Optional[Literal["gemini-2.5-flash", "deepseek-v4-flash"]] = "deepseek-v4-flash"
    # Per-request audit toggle. The frontend persists the user's choice in
    # localStorage and sends it on every /chat. When False, no audit events
    # are written and audit.jsonl is never touched.
    audit_enabled: Optional[bool] = True


class AuditDecisionRequest(BaseModel):
    """Frontend posts here whenever the user clicks an approval-card button."""
    request_id: str
    tool_index: int
    decision: Literal["approve", "use_ai", "reject", "reject_retry", "failed"]
    reason: Optional[str] = None
    audit_enabled: Optional[bool] = True


class ReviewRequest(BaseModel):
    """On-demand review: user selects a region and triggers assumption checks."""
    values: List[List[Any]]
    formulas: List[List[Any]] = []
    address: str = ""
    audit_enabled: Optional[bool] = True


class VarianceRequest(BaseModel):
    """Variance analysis: read-only, sourced from a statement worksheet (v1: IS).

    The frontend chip reads the Income Statement tab's used range and posts it
    here — distinct from the active-selection ChatRequest.context shape.
    """
    values: List[List[Any]]
    # Accepted for forward-compatibility but NOT forwarded to the analysis
    # server — analyse_variance works on values only.
    formulas: List[List[Any]] = []
    address: str = ""
    sheet: str = ""
    # Clearly-trivial materiality threshold (absolute, in the sheet's own units).
    # Line items whose change is below it are split out as immaterial. 0 = no filter.
    clearly_trivial: float = 0.0
    model: Optional[Literal["gemini-2.5-flash", "deepseek-v4-flash"]] = "deepseek-v4-flash"
    audit_enabled: Optional[bool] = True

# ── Main endpoint ─────────────────────────────────────────
@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    selected_model = request.model or "deepseek-v4-flash"
    audit_enabled = bool(request.audit_enabled)

    # Generate one request_id per /chat round. All events emitted during
    # this request — chat_request, reflexion_run(s), hook_check(s),
    # chat_response, and (later, from the frontend) approval_decision(s) —
    # share this id so the renderer can group them.
    request_id = str(uuid.uuid4())

    # 1. Get user message (single-turn: one message per request)
    last_user_message = request.message

    # Audit event 1/3: chat_request — records what the user asked, plus the
    # selection context. For small selections (≤500 cells) we record all
    # values so the audit trail is fully replayable. For large selections
    # we truncate to the first 20 rows to keep audit.jsonl from ballooning.
    if audit_enabled:
        sel_payload = None
        if request.context:
            MAX_AUDIT_CELLS = 500
            total_cells = request.context.rowCount * request.context.columnCount
            if total_cells <= MAX_AUDIT_CELLS:
                audit_values = request.context.values
                truncated = False
            else:
                audit_values = request.context.values[:20]
                truncated = True
            sel_payload = {
                "address": request.context.address,
                "sheet": request.context.sheet,
                "rowCount": request.context.rowCount,
                "columnCount": request.context.columnCount,
                "values": audit_values,
                "truncated": truncated,
            }
        audit.append_event(
            "chat_request",
            {
                "user_message": last_user_message,
                "model": selected_model,
                "selection": sel_payload,
            },
            request_id=request_id,
            enabled=audit_enabled,
        )

    # 2. Build base system prompt
    base_prompt = (
        "You are an expert Excel AI assistant embedded directly in a spreadsheet.\n\n"
        "ROLE: Analyse data, clean text, and write formulas. "
        "Refuse non-spreadsheet topics politely.\n"
        "GROUNDING: Base answers EXCLUSIVELY on provided data. "
        "State clearly if data is missing.\n"
        "FORMAT: Be concise. Use bullet points. "
        "Wrap Excel formulas in backticks e.g. `=SUM(A1:A10)`.\n"
        "TOOLS: Use tools only when the user explicitly requests an action. "
        "For summarise_data and clean_data, trigger them only on clear user intent — "
        "not just because the words appear in the message.\n"
        "FORECAST: When the user asks to predict, forecast, or project future "
        "values from a historical series (e.g. 'predict 2022–2026 from the "
        "2017–2021 trend', 'forecast next year with 20% growth'), you MUST call "
        "forecast_data — do NOT emit write_to_cell calls for forecasts. This "
        "applies even when the forecast spans several cells; the FILL-DOWN rule "
        "below does NOT apply to forecasts.\n"
        "FILL-DOWN: When the user asks to compute a value or formula for "
        "every row of a range (e.g. 'put A+B in column C', 'compute the "
        "totals for column D', 'fill column E with =A*B') — but NOT a forecast "
        "(see FORECAST above) — you MUST emit "
        "ONE write_to_cell call PER ROW of the target range. Do not emit a "
        "single formula and expect the user to drag it down — every cell "
        "must be written explicitly so each one is verified and audited. "
        "Look at the data context to determine the row range (skip header "
        "rows, stop where the source columns end). Example: if the user "
        "says 'put A+B in column C' and the data has rows 2 through 11, "
        "emit ten write_to_cell calls: C2 with =A2+B2, C3 with =A3+B3, "
        "..., C11 with =A11+B11.\n"
        "RETRIES: If the user message starts with "
        "'[Previous proposal was rejected — please retry]', the user has already "
        "approved the original action type and is asking for a corrected version. "
        "DO NOT ask clarifying questions. Call the same tool (typically "
        "write_to_cell) again with corrected arguments that address the reason "
        "for rejection."
    )

    # 3. Build context string
    context_str = ""
    if request.context:
        MAX_CONTEXT_ROWS = 100
        all_rows  = request.context.values
        data_rows = all_rows[:MAX_CONTEXT_ROWS]
        truncated = len(all_rows) > MAX_CONTEXT_ROWS
        label = (
            f"Data (first {MAX_CONTEXT_ROWS} of {request.context.rowCount} rows — "
            f"SELECTION TRUNCATED, more rows exist below):"
            if truncated else
            f"Data (all {len(data_rows)} rows):"
        )
        context_str = (
            f"Range: {request.context.address} on sheet '{request.context.sheet}'\n"
            f"Size: {request.context.rowCount} rows x {request.context.columnCount} cols\n"
            f"{label}\n"
            + "\n".join([", ".join(str(v) for v in row) for row in data_rows])
        )

    final_user_message = last_user_message
    if context_str:
        final_user_message = f"[Excel context]\n{context_str}\n\n[User question]\n{last_user_message}"

    try:
        # 4. First call: intent recognition + response generation
        first_pass = await _call_model(
            selected_model=selected_model,
            user_content=final_user_message,
            system_instruction=base_prompt,
            use_tools=True,
        )
        tool_calls = first_pass["tool_calls"]
        ai_text = first_pass["text"]

        print(f"[DEBUG] Tool calls: {[t['name'] for t in tool_calls]}")

        # 5. Handle skill tools (summarise_data, clean_data)
        skill_tool = next((t for t in tool_calls if t["name"] in app.state.skill_text_names), None)

        if skill_tool:
            try:
                skill_content = await _load_skill_content(skill_tool["name"])
            except RuntimeError:
                return _skills_unavailable_response(request_id, audit_enabled)
            print(f"[DEBUG] Skill triggered: {skill_tool['name']}, file loaded: {skill_content is not None}")

            skill_prompt = base_prompt
            if skill_content:
                skill_prompt += f"\n\n── SKILL INSTRUCTIONS (follow these precisely) ──\n{skill_content}"

            skill_response = await _call_model(
                selected_model=selected_model,
                user_content=final_user_message,
                system_instruction=skill_prompt,
                use_tools=False,
            )
            ai_text = skill_response["text"].strip()

            # Reflexion review on skill response
            review_meta = None
            if contains_formula(ai_text):
                print("[REFLEXION] Starting reflexion on skill response...")
                review_meta = await reflexion_review(
                    ai_text,
                    context_str,
                    selected_model=selected_model,
                    request_id=request_id,
                    audit_enabled=audit_enabled,
                )
                ai_text = review_meta["final_reply"]
                if not review_meta["verified"]:
                    ai_text += f"\n\n---\n**⚠️ Formula could not be fully verified after {review_meta['iterations']} attempts. Please double-check before using.**"

            if audit_enabled:
                audit.append_event(
                    "chat_response",
                    {
                        "skill_used": True,
                        "tool_calls_proposed": [],
                        "reply_excerpt": ai_text[:400],
                    },
                    request_id=request_id,
                    enabled=audit_enabled,
                )


            return {
                "reply": ai_text,
                "tool_calls": [],
                "skill_used": True,
                "review": review_meta,
                "request_id": request_id,
            }

        # 5b. Handle action-skill tools (forecast_data). Two-pass like skills,
        # but the second pass produces a structured action (apply_forecast)
        # under the loaded contract, then falls through to the action path so
        # it goes through pre_write_hook and is shown as one approval card.
        forecast_intent = next((t for t in tool_calls if t["name"] in app.state.skill_action_names), None)
        if forecast_intent:
            try:
                skill_content = await _load_skill_content(forecast_intent["name"])
            except RuntimeError:
                return _skills_unavailable_response(request_id, audit_enabled)
            print(f"[DEBUG] Action-skill triggered: {forecast_intent['name']}, file loaded: {skill_content is not None}")

            forecast_prompt = base_prompt
            if skill_content:
                forecast_prompt += f"\n\n── SKILL INSTRUCTIONS (follow these precisely) ──\n{skill_content}"
            forecast_prompt += (
                "\n\nNow call apply_forecast exactly once: provide the target cells, "
                "the formula for each cell (aligned with the cells), the method, a "
                "one-sentence rationale, and the historical values you based the "
                "forecast on."
            )

            forecast_pass = await _call_model(
                selected_model=selected_model,
                user_content=final_user_message,
                system_instruction=forecast_prompt,
                use_tools=True,
                tools_override=FORECAST_ONLY_TOOLS,
            )
            # Replace tool_calls with the contracted second-pass result and fall
            # through to the action path below.
            tool_calls = forecast_pass["tool_calls"]
            ai_text = forecast_pass["text"]
            print(f"[DEBUG] Forecast pass tool calls: {[t['name'] for t in tool_calls]}")

        # 6. Handle action tools (write_to_cell, create_chart, apply_forecast)
        action_tools = [t for t in tool_calls if t["name"] not in app.state.skill_text_names]

        # Collapse same-column row-pattern writes into one apply_formula_pattern
        # tool_call so the user only confirms once for an N-row fill. Falls
        # back transparently when the run is too short or not uniform.
        action_tools = _collapse_row_pattern_writes(action_tools)

        if action_tools:
            # Run pre_write_hook on every action tool.
            # Note: we do NOT block in the backend — check results are attached
            # as metadata on each tool_call, and the frontend approval UI shows
            # them to the user. The user makes the final call.
            enriched_tools = []
            all_checks = []

            for tool in action_tools:
                hook_result = await pre_write_hook(
                    tool,
                    context_str,
                    context=request.context,
                    selected_model=selected_model,
                    request_id=request_id,
                    audit_enabled=audit_enabled,
                )
                all_checks.extend(hook_result["checks_run"])

                # Attach hook result to the tool_call — frontend renders the
                # approval card based on this.
                enriched_tools.append({
                    "name": tool["name"],
                    "args": tool["args"],
                    "hook_result": {
                        "status": hook_result["status"],
                        "warnings": hook_result["warnings"],
                        "suggestions": hook_result.get("suggestions", []),
                        "checks_run": hook_result["checks_run"],
                        "review_meta": hook_result.get("review_meta"),
                    },
                })

            if audit_enabled:
                audit.append_event(
                    "chat_response",
                    {
                        "skill_used": False,
                        "tool_calls_proposed": [
                            {"name": t["name"], "args": t["args"]} for t in enriched_tools
                        ],
                        "reply_excerpt": (ai_text or "")[:400],
                    },
                    request_id=request_id,
                    enabled=audit_enabled,
                )


            return {
                "reply": ai_text or "",
                "tool_calls": enriched_tools,
                "skill_used": False,
                "checks_run": all_checks,
                "request_id": request_id,
            }

        # 7. Plain text reply — reflexion if formulas present
        print(f"[DEBUG] Plain text reply. Contains formula: {contains_formula(ai_text)}")
        review_meta = None
        if contains_formula(ai_text):
            print("[REFLEXION] Starting reflexion on plain text reply...")
            review_meta = await reflexion_review(
                ai_text,
                context_str,
                selected_model=selected_model,
                request_id=request_id,
                audit_enabled=audit_enabled,
            )
            ai_text = review_meta["final_reply"]
            if not review_meta["verified"]:
                ai_text += f"\n\n---\n**⚠️ Formula could not be fully verified after {review_meta['iterations']} attempts. Please double-check.**"

        if audit_enabled:
            audit.append_event(
                "chat_response",
                {
                    "skill_used": False,
                    "tool_calls_proposed": [],
                    "reply_excerpt": (ai_text or "")[:400],
                },
                request_id=request_id,
                enabled=audit_enabled,
            )

        return {
            "reply": ai_text,
            "tool_calls": [],
            "skill_used": False,
            "review": review_meta,
            "request_id": request_id,
        }

    except Exception as e:
        # Even errors get an audit entry — auditors care about silent failures
        # too. Excerpt only, no stack trace dumped to the file.
        if audit_enabled:
            audit.append_event(
                "chat_response",
                {
                    "skill_used": False,
                    "tool_calls_proposed": [],
                    "reply_excerpt": f"[backend error] {str(e)[:300]}",
                    "error": True,
                },
                request_id=request_id,
                enabled=audit_enabled,
            )
        return {
            "reply": f"⚠️ Backend error: {str(e)}",
            "tool_calls": [],
            "skill_used": False,
            "review": None,
            "request_id": request_id,
        }


# ── Audit endpoints ──────────────────────────────────────────────────────────
@app.post("/audit/decision")
async def audit_decision(req: AuditDecisionRequest):
    """
    Record what the user decided on the approval card. Without this, the
    audit chain is missing its endpoint — we'd have "what was proposed" and
    "what the system flagged" but no "what the human chose".

    Frontend calls this when the user clicks Approve / Use AI's version /
    Reject / Reject & retry on an approval card. The button handler also
    posts a `failed` decision when the actual Excel write throws.

    Non-fatal: never raises. If audit is disabled this is a near-no-op.
    """
    if req.audit_enabled:
        audit.append_event(
            "approval_decision",
            {
                "tool_index": req.tool_index,
                "decision": req.decision,
                "reason": req.reason,
            },
            request_id=req.request_id,
            enabled=True,
        )
    return {"ok": True}


@app.get("/audit/view")
async def audit_view():
    """
    Render audit.jsonl → markdown on demand and return it as a downloadable
    .md file. This is the only place rendering happens now — removed from
    the /chat hot path so every message doesn't pay the full-file I/O cost.
    """
    audit_render.regenerate()
    md_path = audit_render.MD_FILE
    content = ""
    if os.path.exists(md_path):
        with open(md_path, "r", encoding="utf-8") as f:
            content = f.read()
    return PlainTextResponse(
        content or "# Audit Trail\n\n_No events recorded yet._\n",
        media_type="text/markdown",
        headers={"Content-Disposition": 'attachment; filename="audit.md"'},
    )


@app.post("/audit/clear")
async def audit_clear():
    """Wipe audit.jsonl and audit.md. The user owns their audit history."""
    removed = audit.clear()
    return {"ok": True, "removed": removed}


# ── Review Layer (on-demand, read-only assumption checks) ──────────────────────
def _unwrap_tool_result(result) -> dict:
    """Pull the structured dict out of an MCP CallToolResult.

    The tool JSON-encodes its return into a text content block, so that's the
    most version-stable source for an exact-shape payload. structuredContent is
    used as a fallback.
    """
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            return json.loads(text)
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    raise RuntimeError("empty MCP tool result")


@app.post("/review")
async def review_endpoint(request: ReviewRequest):
    """
    Run RULES_REVIEW against the user's selected region. Read-only — never
    modifies cells. The rule computation runs in the excelcat-verify MCP server;
    this endpoint owns request_id generation and the audit chokepoint.
    """
    audit_enabled = bool(request.audit_enabled)
    request_id = str(uuid.uuid4())

    def _finish(located, inspected_cells, results, summary, *, error=False) -> dict:
        # Audit payload carries `address`; the frontend response keeps the
        # original shape (no address). `error` is additive — only present when
        # the verification service was unreachable.
        payload = {
            "address": request.address,
            "located": located,
            "inspected_cells": inspected_cells,
            "results": results,
            "summary": summary,
        }
        if error:
            payload["error"] = True
        audit.append_event(
            "review_run", payload, request_id=request_id, enabled=audit_enabled
        )
        resp = {
            "request_id": request_id,
            "located": located,
            "inspected_cells": inspected_cells,
            "results": results,
            "summary": summary,
        }
        if error:
            resp["error"] = True
        return resp

    session = getattr(app.state, "mcp_verify", None)
    if session is None:
        return _finish(
            {}, {}, [],
            "Review is temporarily unavailable (verification service not running).",
            error=True,
        )

    try:
        result = await session.call_tool(
            "review_assumptions",
            {
                "values": request.values,
                "formulas": request.formulas,
                "address": request.address,
            },
        )
        review = _unwrap_tool_result(result)
    except Exception as e:
        return _finish(
            {}, {}, [],
            f"Review is temporarily unavailable (verification call failed: {e}).",
            error=True,
        )

    return _finish(
        review["located"],
        review["inspected_cells"],
        review["results"],
        review["summary"],
    )


# ── Variance Analysis (on-demand, read-only YoY analysis) ──────────────────────
@app.post("/variance")
async def variance_endpoint(request: VarianceRequest):
    """
    Year-over-year variance analysis over a financial statement (v1: Income
    Statement). Read-only — never writes cells. The deterministic delta
    computation and the two LLM passes run in the excelcat-analysis MCP server;
    this endpoint owns request_id generation, loads the variance contract from
    excelcat-skills, and keeps the audit chokepoint (the `variance_run` event).
    """
    audit_enabled = bool(request.audit_enabled)
    request_id = str(uuid.uuid4())
    selected_model = request.model or "deepseek-v4-flash"

    def _finish(result: dict, *, error=False) -> dict:
        # Audit payload carries `address` + the materiality threshold that was
        # applied, so the trail records what counted as trivial. The frontend
        # response keeps the result shape. `error` is additive — only on a
        # degraded path. (result may also echo clearly_trivial; it overrides and
        # should match.)
        payload = {"address": request.address, "clearly_trivial": request.clearly_trivial, **result}
        if error:
            payload["error"] = True
        audit.append_event(
            "variance_run", payload, request_id=request_id, enabled=audit_enabled
        )
        resp = {"request_id": request_id, **result}
        if error:
            resp["error"] = True
        return resp

    def _error_result(msg: str) -> dict:
        # Empty result shape + error flag; `msg` lands in `summary`, which the
        # frontend renders. Used for both degraded paths (a service down) and
        # honest refusals (statement too large).
        return _finish(
            {
                "current_label": "", "prior_label": "",
                "variance_table": [], "skipped": [],
                "anomalies": [], "cfo_questions": [], "summary": msg,
            },
            error=True,
        )

    # Guard: Pass A puts the whole grid in front of the LLM, so a polluted used
    # range (one stray cell at ZZ10000 balloons it to millions of cells) must be
    # refused here — before the token bill — with a message that says why. A
    # real income statement sits far inside these bounds.
    MAX_VARIANCE_ROWS, MAX_VARIANCE_COLS = 200, 30
    n_rows = len(request.values)
    n_cols = max((len(r) for r in request.values), default=0)
    if n_rows > MAX_VARIANCE_ROWS or n_cols > MAX_VARIANCE_COLS:
        return _error_result(
            f"The sheet's used range is {n_rows} rows × {n_cols} columns — too large to "
            f"analyse (limit {MAX_VARIANCE_ROWS} × {MAX_VARIANCE_COLS}). This usually means "
            "stray cells outside the statement; clear them and try again."
        )

    session = getattr(app.state, "mcp_analysis", None)
    if session is None:
        return _error_result("Variance analysis is temporarily unavailable (analysis service not running).")

    # Load the contract from excelcat-skills (single source of truth for the
    # relationship framework). Without it the analysis loses its guard-rails, so
    # we fail loud rather than silently run a weaker prompt.
    try:
        contract_md = await _load_skill_content("variance_analysis") or ""
    except RuntimeError:
        return _error_result("Variance analysis is temporarily unavailable (skills service not running).")

    try:
        result = _unwrap_tool_result(await session.call_tool(
            "analyse_variance",
            {
                "statement": {
                    "values": request.values,
                    "address": request.address,
                    "sheet": request.sheet,
                },
                "contract_md": contract_md,
                "model": selected_model,
                "clearly_trivial": request.clearly_trivial or 0.0,
            },
        ))
    except Exception as e:
        return _error_result(f"Variance analysis is temporarily unavailable (analysis call failed: {e}).")

    return _finish(result)
