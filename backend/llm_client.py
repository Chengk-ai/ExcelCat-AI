"""
LLM client layer — the single home for "talking to a model".

Extracted from main.py (Phase 3 of the MCP refactor) so that BOTH the FastAPI
orchestrator (main.py) and the excelcat-verify MCP server (mcp_servers/
verify_server.py, which runs the reflexion loop) share one implementation
instead of duplicating it. Owns: model config + clients, the function-calling
tool registry (single source of truth), and the _call_deepseek / _call_model
transport. No audit, no orchestration — those stay in main.py.

load_dotenv is called here with an explicit path so the module works regardless
of which process imports it or what its cwd is (the MCP server is a subprocess).
"""
import os
import asyncio
import json
from typing import Optional

import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# ── Config ────────────────────────────────────────────────
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])


# ── Function calling Definitions ──────────────────────────────
# Single source of truth: the JSON-Schema tool list built by _build_openai_tools().
# DeepSeek and (future) OpenAI/GPT models consume that JSON shape natively; only
# Gemini needs an adapter, so we derive its Tool from the same list — there is no
# second hand-maintained copy to drift out of sync.
def _to_gemini_tool(openai_tools: list) -> types.Tool:
    """Derive a Gemini Tool from the OpenAI-style JSON-Schema tool list.

    google-genai (>=1.x) coerces a plain JSON-Schema dict — lowercase types,
    enum, array items, required — straight into its own Schema, so no
    field-by-field rewrite is needed. Each entry is {"type":"function",
    "function":{name,description,parameters}}; we hand the inner "function"
    dicts to Tool, which is exactly the FunctionDeclaration shape it accepts.
    """
    return types.Tool(
        function_declarations=[t["function"] for t in openai_tools]
    )


def _build_openai_tools():
    """The canonical tool registry (JSON Schema). Source of truth for every
    model: DeepSeek / future GPT consume it directly, Gemini via _to_gemini_tool()."""
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
                    "Use ONLY when the user explicitly asks to find outliers, anomalies, or unusual values. "
                    "Do NOT use when the user merely mentions the data looks 'unusual' in passing."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "analyse_data",
                "description": (
                    "Write a one-paragraph plain English analysis of the selected Excel data, "
                    "covering what the data shows, the most important trend, and a recommendation. "
                    "Use ONLY when the user explicitly asks to analyse the data or wants a written narrative. "
                    "Do NOT use for bullet-point summaries — that is summarise_data's job."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "forecast_data",
                "description": (
                    "Project future values from a historical series (e.g. 'predict 2022–2026 "
                    "from the 2017–2021 trend', 'forecast next year's revenue'). "
                    "Use ONLY when the user explicitly asks to predict, forecast, or project "
                    "future values. Do NOT use for describing or summarising existing data."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]


OPENAI_TOOLS = _build_openai_tools()

# Gemini's Tool, derived from the canonical list above — not hand-maintained.
tools = _to_gemini_tool(OPENAI_TOOLS)


# ── Forecast structured action (exposed only on the contracted second pass) ──
# Not advertised on the intent pass — the model only sees this after
# forecast.md has been loaded into the prompt, so every forecast is produced
# under the contract. apply_forecast carries the whole batch in one call, which
# guarantees a single approval card regardless of method.
_APPLY_FORECAST_OPENAI = {
    "type": "function",
    "function": {
        "name": "apply_forecast",
        "description": (
            "Emit a complete forecast as ONE call: every target cell, the formula "
            "for each cell, the method used, a one-sentence rationale, and the "
            "historical values the forecast is based on."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "cells": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Target cell addresses in order, e.g. ['M23','N23','O23']",
                },
                "values": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "The formula for each cell, aligned with 'cells'",
                },
                "method": {"type": "string", "description": "One of: linear, exponential, growth_rate"},
                "rationale": {
                    "type": "string",
                    "description": "One sentence tying the method/rate to the business context",
                },
                "history_values": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "The actual historical figures the forecast is based on",
                },
                "assumed_growth_rate": {
                    "type": "number",
                    "description": "Optional explicit annual growth rate as a decimal, e.g. 0.2 for 20%",
                },
            },
            "required": ["cells", "values", "method", "rationale"],
        },
    },
}

# Tool set for the contracted forecast pass — apply_forecast only, per backend.
# Gemini side derived from the OpenAI spec, same single-source rule as above.
FORECAST_ONLY_TOOLS = {
    "openai": [_APPLY_FORECAST_OPENAI],
    "gemini": _to_gemini_tool([_APPLY_FORECAST_OPENAI]),
}


# ── Transport ─────────────────────────────────────────────
async def _call_deepseek(
    model: str,
    user_content: str,
    system_instruction: Optional[str] = None,
    use_tools: bool = False,
    tools_override: Optional[list] = None,
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
        payload["tools"] = tools_override or OPENAI_TOOLS
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
    tools_override: Optional[dict] = None,
) -> dict:
    # tools_override (when given) restricts which tools the model may call this
    # call. Shape: {"openai": [...openai tool dicts...], "gemini": types.Tool}.
    # Used by the forecast second pass to expose only apply_forecast.
    #
    # Future GPT/OpenAI models plug in here: add a branch that reuses OPENAI_TOOLS
    # (the canonical JSON-Schema list) directly — no new tool definitions needed,
    # only a client call. Only Gemini ever needs the _to_gemini_tool() adapter.
    if selected_model == "deepseek-v4-flash":
        return await _call_deepseek(
            model=DEEPSEEK_MODEL,
            user_content=user_content,
            system_instruction=system_instruction,
            use_tools=use_tools,
            tools_override=(tools_override or {}).get("openai"),
        )

    # Gemini SDK is synchronous — run in a thread so it doesn't block the
    # event loop either. Same principle as the DeepSeek fix.
    config = None
    if system_instruction or use_tools:
        gemini_tools = None
        if use_tools:
            gemini_tools = [(tools_override or {}).get("gemini") or tools]
        config = types.GenerateContentConfig(
            system_instruction=system_instruction if system_instruction else None,
            tools=gemini_tools,
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
