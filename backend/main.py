from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from typing import List, Optional, Any, Literal
import asyncio
import os
import re
import json
import uuid
import httpx
from google import genai
from google.genai import types

import audit
import audit_render
from rules import RULES, RULES_REVIEW
from rules.base import ReviewContext
from rules.financial.param_locator import locate_all, IS_PERCENTAGE
from rules.financial.row_classifier import count_inspectable_cells
from rules.financial.horizontal_formula_consistency import MIN_DATA_CELLS
from rules.financial.hardcode_trend_anomaly import MIN_VALUES

# ── Config ────────────────────────────────────────────────
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Skills loader ─────────────────────────────────────────
SKILLS_DIR = os.path.join(os.path.dirname(__file__), "skills") # join the skills directory and the filename

SKILL_FILE_MAP = { 
    "summarise_data": "summarise.md", 
    "clean_data":     "clean_data.md",
    "find_outliers":  "find_outliers.md",
    "analyse_data":   "analyse_data.md",
}

def load_skill_by_tool(tool_name: str) -> Optional[str]:
    """Load skill file content by function call name"""
    filename = SKILL_FILE_MAP.get(tool_name) # get the filename from the map
    if not filename:
        return None
    path = os.path.join(SKILLS_DIR, filename) # join the skills directory and the filename  
    if os.path.exists(path): # if the path exists, read the file
        with open(path, "r", encoding="utf-8") as f:
            return f.read() # return the file content
    return None

# ── Function calling Definitions ──────────────────────────────
tools = types.Tool(
    function_declarations=[

        # ── Excel action tools ──
        types.FunctionDeclaration(
            name="write_to_cell",
            description=(
                "Write a value or formula to a specific Excel cell. "
                "Use this when the user explicitly asks to write, insert, or put something into a cell."
            ),
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "cell": types.Schema(type="STRING", description="Cell address e.g. A1, B3"),
                    "value": types.Schema(type="STRING", description="Value or formula e.g. =SUM(A1:A10)"),
                },
                required=["cell", "value"]
            )
        ),

        types.FunctionDeclaration(
            name="create_chart",
            description=(
                "Create a chart from the currently selected Excel range. "
                "Use this when the user explicitly asks to create, draw, or generate a chart or graph. "
                "Choose the most appropriate chart type based on the data."
            ),
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "chart_type": types.Schema(
                        type="STRING",
                        description="Chart type: ColumnClustered, Line, Pie, Bar, or Area"
                    ),
                    "title": types.Schema(type="STRING", description="Chart title"),
                },
                required=["chart_type"]
            )
        ),

        # ── Skill tools (intent recognition) ──
        types.FunctionDeclaration(
            name="summarise_data",
            description=(
                "Summarise the selected Excel data with key insights, trends, and statistics. "
                "Use ONLY when the user explicitly asks to summarise, analyse, or get an overview of the data. "
                "Do NOT use if the user merely mentions the word 'summary' in passing."
            ),
        ),

        types.FunctionDeclaration(
            name="clean_data",
            description=(
                "Identify and fix data quality issues such as inconsistent formatting, blank cells, and duplicates. "
                "Use ONLY when the user explicitly asks to clean, fix, or tidy the data. "
                "Do NOT use if the user merely describes the data as 'clean'."
            ),
        ),
        types.FunctionDeclaration(
            name="find_outliers",
            description=(
                "Identify anomalous or unusual values in the selected Excel data. "
                "Use ONLY when the user explicitly asks to find outliers, anomalies, or unusual values. "
                "Do NOT use when the user merely mentions the data looks 'unusual' in passing."
            ),
        ),

        types.FunctionDeclaration(
            name="analyse_data",
            description=(
                "Write a one-paragraph plain English analysis of the selected Excel data, "
                "covering what the data shows, the most important trend, and a recommendation. "
                "Use ONLY when the user explicitly asks to analyse the data or wants a written narrative. "
                "Do NOT use for bullet-point summaries — that is summarise_data's job."
            ),
        ),
    ]
)


def _build_openai_tools():
    """Convert Gemini function declarations to OpenAI-compatible tools schema."""
    return [
        {
            "type": "function",
            "function": {
                "name": "write_to_cell",
                "description": (
                    "Write a value or formula to a specific Excel cell. "
                    "Use this when the user explicitly asks to write, insert, or put something into a cell."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cell": {"type": "string", "description": "Cell address e.g. A1, B3"},
                        "value": {"type": "string", "description": "Value or formula e.g. =SUM(A1:A10)"},
                    },
                    "required": ["cell", "value"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "create_chart",
                "description": (
                    "Create a chart from the currently selected Excel range. "
                    "Use this when the user explicitly asks to create, draw, or generate a chart or graph. "
                    "Choose the most appropriate chart type based on the data."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "chart_type": {
                            "type": "string",
                            "description": "Chart type: ColumnClustered, Line, Pie, Bar, or Area",
                        },
                        "title": {"type": "string", "description": "Chart title"},
                    },
                    "required": ["chart_type"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "summarise_data",
                "description": (
                    "Summarise the selected Excel data with key insights, trends, and statistics. "
                    "Use ONLY when the user explicitly asks to summarise, analyse, or get an overview of the data. "
                    "Do NOT use if the user merely mentions the word 'summary' in passing."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "clean_data",
                "description": (
                    "Identify and fix data quality issues such as inconsistent formatting, blank cells, and duplicates. "
                    "Use ONLY when the user explicitly asks to clean, fix, or tidy the data. "
                    "Do NOT use if the user merely describes the data as 'clean'."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "find_outliers",
                "description": (
                    "Identify anomalous or unusual values in the selected Excel data. "
                    "Use ONLY when the user explicitly asks to find outliers, anomalies, or unusual values."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "analyse_data",
                "description": (
                    "Write a one-paragraph plain English analysis of the selected Excel data. "
                    "Use ONLY when the user explicitly asks to analyse the data or wants a written narrative."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]


OPENAI_TOOLS = _build_openai_tools()


async def _call_deepseek(
    model: str,
    user_content: str,
    system_instruction: Optional[str] = None,
    use_tools: bool = False,
) -> dict:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured in backend/.env")

    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": user_content})

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
    }
    if use_tools:
        payload["tools"] = OPENAI_TOOLS
        payload["tool_choice"] = "auto"

    async with httpx.AsyncClient(timeout=60) as client:
        try:
            resp = await client.post(
                f"{DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions",
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            body = e.response.text
            raise RuntimeError(f"DeepSeek API error {e.response.status_code}: {body}") from e

    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}

    text = message.get("content") or ""
    parsed_tool_calls = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        raw_args = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
        except json.JSONDecodeError:
            args = {}
        parsed_tool_calls.append({
            "name": fn.get("name", ""),
            "args": args,
        })

    return {"text": text.strip(), "tool_calls": parsed_tool_calls}


async def _call_model(
    selected_model: str,
    user_content: str,
    system_instruction: Optional[str] = None,
    use_tools: bool = False,
) -> dict:
    if selected_model == "deepseek-v4-flash":
        return await _call_deepseek(
            model=DEEPSEEK_MODEL,
            user_content=user_content,
            system_instruction=system_instruction,
            use_tools=use_tools,
        )

    # Gemini SDK is synchronous — run in a thread so it doesn't block the
    # event loop either. Same principle as the DeepSeek fix.
    config = None
    if system_instruction or use_tools:
        config = types.GenerateContentConfig(
            system_instruction=system_instruction if system_instruction else None,
            tools=[tools] if use_tools else None,
        )

    def _gemini_sync():
        return gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[user_content],
            config=config,
        )

    response = await asyncio.to_thread(_gemini_sync)

    parsed_tool_calls = []
    text = ""
    for part in response.parts:
        if hasattr(part, "function_call") and part.function_call:
            fc = part.function_call
            parsed_tool_calls.append({
                "name": fc.name,
                "args": dict(fc.args) if fc.args else {}
            })
        elif hasattr(part, "text") and part.text:
            text += part.text
    return {"text": text.strip(), "tool_calls": parsed_tool_calls}

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
) -> dict: # review the original reply
    """
    Reflexion loop:
      Iteration 1: Critique → if wrong, Revise
      Iteration 2: Re-verify revised → if wrong, Revise again
      Iteration 3: Final re-verify
    Returns:
      {
        "final_reply": str,    # best version after corrections
        "verified": bool,      # True if passed final review
        "iterations": int,     # how many loops ran
        "log": list            # what happened each iteration
      }

    Audit: when request_id is provided and audit_enabled is True, emits one
    `reflexion_run` event at the end of the loop. The event's payload
    includes the full critique chain so an auditor can replay every iteration
    of the critic's reasoning.
    """
    current_reply = original_reply
    log = []

    for i in range(1, max_iterations + 1):
        print(f"[REFLEXION] Iteration {i}/{max_iterations}")

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
         # create the critique prompt
        critique = (await _call_model(selected_model, critique_prompt))["text"].strip()
        print(f"[REFLEXION] Critique {i}: {critique}")

        # Verified — stop loop early
        if critique == "✓ Verified":
            log.append({"iteration": i, "critique": "✓ Verified", "revised": False})
            result = {
                "final_reply": current_reply,
                "verified": True,
                "iterations": i,
                "log": log
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

        # Step 2: Revise (only if not last iteration)
        if i < max_iterations:
            revise_prompt = f"""You are an Excel formula expert.
            The following response contains formula errors. Fix ONLY the formulas — keep all other text exactly the same.
            Original response: {current_reply}
            Error identified: {critique}
            User's data context: {context_str}
            Return the full corrected response. Do not add any explanation or preamble."""
            # create the revise prompt
            revised_reply = (await _call_model(selected_model, revise_prompt))["text"].strip()
            print(f"[REFLEXION] Revised {i}: {revised_reply[:150]}")

            log.append({
                "iteration": i,
                "critique": critique,
                "revised": True,
            })
            current_reply = revised_reply
        else:
            # Last iteration, still not verified
            log.append({"iteration": i, "critique": critique, "revised": False})

    # Max iterations reached without verification
    print(f"[REFLEXION] Max iterations reached, returning lastest version")
    result = {
        "final_reply": current_reply,
        "verified": False,
        "iterations": max_iterations,
        "log": log
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

    # ── Check 2: Data Integrity (deterministic rules) ──
    for rule in RULES:
        if rule.applies_to(tool_call["name"]):
            results = rule.check(tool_call, context)
            for r in results:
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


def _collapse_row_pattern_writes(tool_calls: list[dict]) -> list[dict]:
    """
    Walk the tool_calls list and collapse runs of ≥3 write_to_cell calls that:
      (1) target the same column,
      (2) on contiguous rows (after sorting),
      (3) and whose values reduce to the same row-templated form.

    Returns a possibly-shorter list where each collapsed run becomes one
    apply_formula_pattern tool_call. Any tool_call that doesn't fit a run
    is preserved unchanged.
    """
    if not tool_calls:
        return tool_calls

    out: list[dict] = []
    i = 0
    n = len(tool_calls)
    while i < n:
        t = tool_calls[i]
        if t.get("name") != "write_to_cell":
            out.append(t)
            i += 1
            continue

        # Try to extend a run starting at i.
        run = [t]
        parsed_first = _parse_cell(t.get("args", {}).get("cell", ""))
        first_value = t.get("args", {}).get("value", "")
        if not parsed_first or first_value == "":
            out.append(t)
            i += 1
            continue
        first_col, first_row = parsed_first
        first_template = _row_template(first_value, first_row)

        j = i + 1
        while j < n:
            tj = tool_calls[j]
            if tj.get("name") != "write_to_cell":
                break
            parsed = _parse_cell(tj.get("args", {}).get("cell", ""))
            value = tj.get("args", {}).get("value", "")
            if not parsed or value == "":
                break
            col_j, row_j = parsed
            # Same column, contiguous row.
            if col_j != first_col:
                break
            # We don't require strict +1 ordering from the LLM; we just
            # require the *set* of rows to be contiguous. Build greedily,
            # validate after.
            template_j = _row_template(value, row_j)
            if template_j != first_template:
                break
            run.append(tj)
            j += 1

        # Validate the run: ≥3, rows form a contiguous span when sorted.
        if len(run) >= 3:
            rows = sorted(_parse_cell(r["args"]["cell"])[1] for r in run)
            if rows == list(range(rows[0], rows[0] + len(rows))):
                # Reorder run by row so cells/values are sorted top-to-bottom.
                run.sort(key=lambda r: _parse_cell(r["args"]["cell"])[1])
                cells  = [r["args"]["cell"] for r in run]
                values = [r["args"]["value"] for r in run]
                rng    = f"{first_col}{rows[0]}:{first_col}{rows[-1]}"
                out.append({
                    "name": "apply_formula_pattern",
                    "args": {
                        "pattern": first_template,
                        "range":   rng,
                        "cells":   cells,
                        "values":  values,
                    },
                })
                i = j
                continue

        # Run didn't qualify — emit just the first call and advance by 1.
        out.append(t)
        i += 1

    return out


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
        "FILL-DOWN: When the user asks to compute a value or formula for "
        "every row of a range (e.g. 'put A+B in column C', 'compute the "
        "totals for column D', 'fill column E with =A*B'), you MUST emit "
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
        data_rows = request.context.values[:10]
        context_str = (
            f"Range: {request.context.address} on sheet '{request.context.sheet}'\n"
            f"Size: {request.context.rowCount} rows x {request.context.columnCount} cols\n"
            f"Data (first 10 rows):\n"
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
        skill_tool = next((t for t in tool_calls if t["name"] in SKILL_FILE_MAP), None)

        if skill_tool:
            skill_content = load_skill_by_tool(skill_tool["name"])
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

        # 6. Handle action tools (write_to_cell, create_chart)
        action_tools = [t for t in tool_calls if t["name"] not in SKILL_FILE_MAP]

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


@app.post("/review")
async def review_endpoint(request: ReviewRequest):
    """
    Run RULES_REVIEW against the user's selected region. Read-only — never
    modifies cells. Returns a report the frontend renders as a static card.
    """
    audit_enabled = bool(request.audit_enabled)
    request_id = str(uuid.uuid4())

    review_ctx = ReviewContext(
        values=request.values,
        formulas=request.formulas,
        address=request.address,
    )

    located = {}
    for key, label in _REVIEW_PARAM_LABELS.items():
        found = locate_all(key, request.values, request.address)
        if found:
            located[label] = [
                {"value": _fmt_review_value(key, p.value), "cell": p.cell}
                for p in found
            ]

    # Proof-of-work for the row-level rules: count how many data cells of each
    # kind actually cleared the inspection bar. Counting cells (not rows) lets
    # a mixed row's formula and hardcode segments both show up. Without this, a
    # clean trend row looks identical to "we didn't even look" — same ambiguity
    # v1 hit with param locator.
    inspected_cells = count_inspectable_cells(
        request.formulas, MIN_DATA_CELLS, MIN_VALUES
    )

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

    payload = {
        "address": request.address,
        "located": located,
        "inspected_cells": inspected_cells,
        "results": [
            {"rule_id": r.rule_id, "level": r.level, "message": r.message}
            for r in results
        ],
        "summary": summary,
    }

    audit.append_event(
        "review_run",
        payload,
        request_id=request_id,
        enabled=audit_enabled,
    )

    return {
        "request_id": request_id,
        "located": located,
        "inspected_cells": inspected_cells,
        "results": payload["results"],
        "summary": summary,
    }