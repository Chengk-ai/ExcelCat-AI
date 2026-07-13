"""
LLM client layer — the single home for "talking to a model".

Extracted from main.py (Phase 3 of the MCP refactor) so that BOTH the FastAPI
orchestrator (main.py) and the excelcat-verify MCP server (mcp_servers/
verify_server.py, which runs the reflexion loop) share one implementation
instead of duplicating it. Owns: the model registry (routing key → provider +
real model id), the function-calling tool registry (single source of truth),
and the _call_openai_compat / _call_model transport. No audit, no
orchestration — those stay in main.py.

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

# ── Model registry ────────────────────────────────────────
# Single source of truth for every model the product can offer. The KEY is the
# routing key — what the frontend dropdown sends and what travels through the
# API. `model_id` is what actually goes on the wire; the audit trail must
# record model_id (recording only the routing key would let a .env override
# silently misattribute outputs). Adding a model = one entry here + one
# <option> in taskpane.html.
#
# provider:
#   "openai_compat" — OpenAI-style /chat/completions (DeepSeek, OpenAI, and any
#                     compatible vendor); needs base_url + api_key_env.
#   "gemini"        — Google GenAI SDK.
# temperature is optional — omitted means "don't send it" (some GPT-5.x models
# reject the parameter).
MODEL_REGISTRY = {
    "gemini-2.5-flash": {
        "provider": "gemini",
        "model_id": os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        "label": "Gemini 2.5 Flash",
    },
    "deepseek-v4-flash": {
        "provider": "openai_compat",
        "model_id": os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "api_key_env": "DEEPSEEK_API_KEY",
        "label": "DeepSeek V4 Flash",
        "temperature": 0.2,
    },
    "gpt-5.4-mini": {
        "provider": "openai_compat",
        "model_id": os.getenv("OPENAI_MODEL", "gpt-5.4-mini"),
        "base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        "api_key_env": "OPENAI_API_KEY",
        "label": "GPT-5.4 mini",
    },
}

# Primary swapped to Gemini (July 2026): DeepSeek's latency kept tripping the
# frontend's 90s /chat timeout on long reflexion chains; Gemini rarely does.
# DeepSeek stays selectable as the fallback.
DEFAULT_MODEL = "gemini-2.5-flash"


def resolve_model_id(selected_model: str) -> str:
    """The real on-the-wire model id for a routing key — audit records THIS."""
    entry = MODEL_REGISTRY.get(selected_model)
    return entry["model_id"] if entry else selected_model

# Gemini is the FALLBACK model, so a missing key must not stop DeepSeek-primary
# operation — and this module is imported by main.py AND the MCP servers, so a
# crash at import would take every capability down at once. The client is
# created lazily on the first Gemini call and fails loud there instead.
_gemini_client: Optional[genai.Client] = None


def _get_gemini_client() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured in backend/.env")
        # 30s default per call (HttpOptions.timeout is in MILLISECONDS); callers
        # override it per request via GenerateContentConfig.http_options in
        # _call_model. Without a cap the SDK waits indefinitely — a hung Gemini
        # call was the "spinner never stops" failure mode on /review and
        # /variance.
        _gemini_client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=30_000),
        )
    return _gemini_client


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


# ── Cleaning structured action (exposed only on the contracted second pass) ──
# Same pattern as apply_forecast: the model only sees this after clean_data.md
# (v4, action contract) is in the prompt. The four aligned arrays ARE the audit
# record: old_values gets verified against the sheet and new_values against the
# declared fix_type, both deterministically (rules/clean_integrity.py) — there
# is no LLM anywhere in the verification path for cleaning.
_APPLY_CLEANING_OPENAI = {
    "type": "function",
    "function": {
        "name": "apply_cleaning",
        "description": (
            "Emit the complete set of mechanical cleaning fixes as ONE call: "
            "target cells, the exact current value of each, the cleaned value, "
            "and the fix type applied — plus notes for advisory-only findings."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "cells": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Target cell addresses, e.g. ['A3','D7']",
                },
                "old_values": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Current content of each cell, copied EXACTLY from the "
                        "data context, aligned with 'cells'"
                    ),
                },
                "new_values": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Cleaned value for each cell — must be exactly the "
                        "declared fix_type applied to the old value"
                    ),
                },
                "fix_types": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["trim", "case_title", "case_upper", "case_lower"],
                    },
                    "description": "The mechanical fix applied to each cell, aligned with 'cells'",
                },
                "notes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Advisory findings NOT actioned (blank cells, duplicate "
                        "rows, date formats, currency symbols) — one CONCRETE "
                        "plain-English finding per item: name the cells, quote "
                        "the offending value, state the problem. No markdown."
                    ),
                },
            },
            "required": ["cells", "old_values", "new_values", "fix_types"],
        },
    },
}

CLEAN_ONLY_TOOLS = {
    "openai": [_APPLY_CLEANING_OPENAI],
    "gemini": _to_gemini_tool([_APPLY_CLEANING_OPENAI]),
}


# ── DCF structured action (exposed only on the contracted /dcf pass) ──
# Same declared-vs-actual pattern as apply_cleaning: the scalar/array metadata
# is the model's declaration, and deterministic rules (dcf_sanity, dcf_integrity)
# verify the emitted sheets embody it. Chip-only — never advertised on the /chat
# intent pass, so this tool does not join OPENAI_TOOLS.
_DRIVER_ARRAY = {"type": "array", "items": {"type": "number"}}

_APPLY_DCF_TEMPLATE_OPENAI = {
    "type": "function",
    "function": {
        "name": "apply_dcf_template",
        "description": (
            "Emit the complete two-sheet DCF valuation as ONE call: every cell "
            "of the fixed WACC and DCF templates, plus the declared assumptions, "
            "drivers, historical series and per-assumption rationale."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sheets": {
                    "type": "array",
                    "description": "Exactly two entries: the WACC sheet then the DCF sheet",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "'WACC' or 'DCF'"},
                            "cells": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Cell addresses, e.g. ['A1','B3']",
                            },
                            "values": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Aligned with 'cells': labels, plain numbers "
                                    "(assumptions/historicals) or '=' formulas"
                                ),
                            },
                        },
                        "required": ["name", "cells", "values"],
                    },
                },
                "wacc_components": {
                    "type": "object",
                    "description": "The CAPM inputs as declared on the WACC sheet (decimals)",
                    "properties": {
                        "rf": {"type": "number"},
                        "beta": {"type": "number"},
                        "mrp": {"type": "number"},
                        "cost_of_debt": {"type": "number"},
                        "tax_rate": {"type": "number"},
                        "debt": {"type": "number"},
                        "equity": {"type": "number"},
                    },
                    "required": ["rf", "beta", "mrp", "cost_of_debt", "tax_rate", "debt", "equity"],
                },
                "wacc": {"type": "number", "description": "The resulting WACC as a decimal"},
                "terminal_growth": {"type": "number", "description": "TGR as a decimal"},
                "drivers": {
                    "type": "object",
                    "description": "Per-forecast-year driver assumptions, one value per year",
                    "properties": {
                        "revenue_growth": _DRIVER_ARRAY,
                        "ebit_margin": _DRIVER_ARRAY,
                        "tax_pct_ebit": _DRIVER_ARRAY,
                        "dna_pct_sales": _DRIVER_ARRAY,
                        "capex_pct_sales": _DRIVER_ARRAY,
                        "dnwc_pct_sales": _DRIVER_ARRAY,
                    },
                    "required": ["revenue_growth", "ebit_margin", "tax_pct_ebit",
                                 "dna_pct_sales", "capex_pct_sales", "dnwc_pct_sales"],
                },
                "forecast_years": {"type": "integer"},
                "historical": {
                    "type": "object",
                    "description": (
                        "The derived historical series exactly as supplied "
                        "(echoed for the deterministic provenance check)"
                    ),
                    "properties": {
                        "revenue": _DRIVER_ARRAY,
                        "ebit": _DRIVER_ARRAY,
                        "taxes": _DRIVER_ARRAY,
                        "dna": _DRIVER_ARRAY,
                        "capex": _DRIVER_ARRAY,
                        "dnwc": _DRIVER_ARRAY,
                        "cash": {"type": "number"},
                        "debt": {"type": "number"},
                    },
                    "required": ["revenue", "ebit"],
                },
                "shares": {"type": "number", "description": "Shares outstanding, if supplied"},
                "current_price": {"type": "number", "description": "Current share price, if supplied"},
                # The WACC/TGR cell addresses are NOT declared here — the
                # template pins them (dcf.md → WACC!B16, DCF!B4) and the
                # integrity rule holds them as constants; a second declaration
                # would only drift. driver_rows stays declared because the
                # driver rows hold per-run values the rule must find.
                "assumption_cells": {
                    "type": "object",
                    "description": "Where the per-year driver assumptions live",
                    "properties": {
                        "driver_rows": {
                            "type": "object",
                            "description": "Driver name → DCF-sheet row number (per the fixed template)",
                            "properties": {
                                "revenue_growth": {"type": "integer"},
                                "ebit_margin": {"type": "integer"},
                                "tax_pct_ebit": {"type": "integer"},
                                "dna_pct_sales": {"type": "integer"},
                                "capex_pct_sales": {"type": "integer"},
                                "dnwc_pct_sales": {"type": "integer"},
                            },
                        },
                    },
                    "required": ["driver_rows"],
                },
                "rationale": {
                    "type": "object",
                    "description": "One sentence per headline assumption",
                    "properties": {
                        "wacc": {"type": "string"},
                        "terminal_growth": {"type": "string"},
                        "revenue_growth": {"type": "string"},
                        "ebit_margin": {"type": "string"},
                        "tax_pct_ebit": {"type": "string"},
                        "dna_pct_sales": {"type": "string"},
                        "capex_pct_sales": {"type": "string"},
                        "dnwc_pct_sales": {"type": "string"},
                    },
                    "required": ["wacc", "terminal_growth"],
                },
            },
            "required": ["sheets", "wacc_components", "wacc", "terminal_growth",
                         "drivers", "forecast_years", "historical",
                         "assumption_cells", "rationale"],
        },
    },
}

DCF_ONLY_TOOLS = {
    "openai": [_APPLY_DCF_TEMPLATE_OPENAI],
    "gemini": _to_gemini_tool([_APPLY_DCF_TEMPLATE_OPENAI]),
}


# ── Transport ─────────────────────────────────────────────
# One shared client so every OpenAI-compat call reuses pooled connections
# instead of paying a fresh TCP+TLS handshake — a single /chat round with
# reflexion can make several calls. Lives for the process lifetime; no explicit
# close. The 30s default per call is sized for /chat's 90s frontend budget —
# one slow call must not eat two-thirds of it. Callers with a bigger budget
# (variance: 150s in variance.js, whole-sheet prompts) pass timeout_s instead
# of inheriting the chat constraint.
DEFAULT_TIMEOUT_S = 30

_http_client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_S)


async def _call_openai_compat(
    entry: dict,
    user_content: str,
    system_instruction: Optional[str] = None,
    use_tools: bool = False,
    tools_override: Optional[list] = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict:
    """Transport for any OpenAI-style /chat/completions vendor (DeepSeek, OpenAI…).

    `entry` is a MODEL_REGISTRY value: model_id, base_url, api_key_env, label,
    optional temperature. The API key is resolved per call so a missing key only
    fails the model that needs it (fail-loud, per capability)."""
    api_key = os.getenv(entry["api_key_env"])
    if not api_key:
        raise RuntimeError(f"{entry['api_key_env']} is not configured in backend/.env")

    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": user_content})

    payload = {
        "model": entry["model_id"],
        "messages": messages,
    }
    if "temperature" in entry:
        payload["temperature"] = entry["temperature"]
    if use_tools:
        payload["tools"] = tools_override or OPENAI_TOOLS
        payload["tool_choice"] = "auto"

    try:
        resp = await _http_client.post(
            f"{entry['base_url'].rstrip('/')}/chat/completions",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            timeout=timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.TimeoutException as e:
        # str(ReadTimeout()) is EMPTY — without this the user sees a blank
        # "Backend error:". Say what happened and what to do about it.
        raise RuntimeError(
            f"{entry['label']} took longer than {timeout_s:g}s on a single call — "
            f"the provider is slow right now. Try again, or switch model."
        ) from e
    except httpx.HTTPStatusError as e:
        body = e.response.text
        raise RuntimeError(f"{entry['label']} API error {e.response.status_code}: {body}") from e
    except httpx.HTTPError as e:
        # Connection-level failures can also stringify poorly; repr never does.
        raise RuntimeError(f"{entry['label']} connection error: {e!r}") from e
    except json.JSONDecodeError as e:
        # A 200 with an empty/non-JSON body — typically a gateway timing out
        # upstream. Raw, this surfaces as "Expecting value: line 1 column 1".
        raise RuntimeError(
            f"{entry['label']} returned an empty or malformed response — usually "
            f"a provider-side timeout. Try again, or switch model."
        ) from e

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
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict:
    # tools_override (when given) restricts which tools the model may call this
    # call. Shape: {"openai": [...openai tool dicts...], "gemini": types.Tool}.
    # Used by the forecast second pass to expose only apply_forecast.
    #
    # Dispatch is registry-driven: the routing key resolves to a provider and a
    # real model id. Only Gemini needs the _to_gemini_tool() adapter; every
    # OpenAI-compatible vendor shares one transport and OPENAI_TOOLS as-is.
    entry = MODEL_REGISTRY.get(selected_model)
    if entry is None:
        raise RuntimeError(
            f"Unknown model '{selected_model}' — add it to MODEL_REGISTRY in llm_client.py"
        )

    if entry["provider"] == "openai_compat":
        return await _call_openai_compat(
            entry,
            user_content=user_content,
            system_instruction=system_instruction,
            use_tools=use_tools,
            tools_override=(tools_override or {}).get("openai"),
            timeout_s=timeout_s,
        )

    # Gemini SDK is synchronous — run in a thread so it doesn't block the
    # event loop either. Same principle as the DeepSeek fix.
    gemini_tools = None
    if use_tools:
        gemini_tools = [(tools_override or {}).get("gemini") or tools]
    config = types.GenerateContentConfig(
        system_instruction=system_instruction if system_instruction else None,
        tools=gemini_tools,
        # Per-request override of the client-level default; HttpOptions.timeout
        # is in MILLISECONDS.
        http_options=types.HttpOptions(timeout=int(timeout_s * 1000)),
    )

    def _gemini_sync():
        return _get_gemini_client().models.generate_content(
            model=entry["model_id"],
            contents=[user_content],
            config=config,
        )

    try:
        response = await asyncio.to_thread(_gemini_sync)
    except httpx.TimeoutException as e:
        raise RuntimeError(
            f"{entry['label']} took longer than {timeout_s:g}s on a single call — "
            f"the provider is slow right now. Try again, or switch model."
        ) from e
    except json.JSONDecodeError as e:
        # The SDK parses the HTTP body as JSON; an empty/truncated body (a
        # gateway timing out upstream) escapes as a raw
        # "Expecting value: line 1 column 1" otherwise.
        raise RuntimeError(
            f"{entry['label']} returned an empty or malformed response — usually "
            f"a provider-side timeout. Try again, or switch model."
        ) from e

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
