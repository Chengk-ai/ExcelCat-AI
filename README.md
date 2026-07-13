# ExcelCat AI

**An Excel add-in for verifiable, LLM-assisted financial analysis.**

ExcelCat AI is a Microsoft Excel task-pane add-in for accountants and financial analysts. It differs from general-purpose spreadsheet assistants in one deliberate way: it prioritises **verifiability over automation**. Every proposed cell write passes through a deterministic verification layer and an LLM self-critique loop *before* the user sees it; nothing reaches the workbook without explicit approval; and every step — request, checks, approval, execution — is recorded in an append-only audit trail exportable for review.

The motivation comes from the accounting profession itself. Recent guidance from the FRC, CCAB and ICAEW (2024–2026) converges on one position: the human remains fully accountable for AI-assisted work, so AI use must be explainable, repeatable and subject to meaningful review. Standard spreadsheet AI leaves no provenance in the workbook — nothing distinguishes the numbers a human entered from the ones a model wrote. ExcelCat AI is designed so that accountability is engineered in, not left to user discipline.

## Design requirements

The system is built against six requirements derived from published professional guidance and from baseline testing of a commercial spreadsheet assistant:

| # | Requirement |
|---|-------------|
| R1 | Every proposed write passes a **fixed, predefined set of checks**, independent of what the model happens to notice on a given run |
| R2 | The user sees the check results **before** approving a write |
| R3 | The audit record is **append-only**; disabling or clearing it is itself recorded |
| R4 | Checks cover formula consistency, hardcoded values where formulas belong, and sanity ranges for valuation assumptions (WACC, terminal growth, beta, tax rate) |
| R5 | Check results are **structured and comparable across runs**, not free text |
| R6 | For any write, the system can state which checks ran — "checked, found nothing" is distinguishable from "never checked" |

## How a write happens

1. A chat request carries the user's message plus the live selection: values, formulas, address.
2. The behaviour expected of the model is defined in a Markdown **skill specification** (`backend/skills/`), injected into the prompt at runtime. The specification an auditor reads is exactly the one the model receives — auditing a capability reduces to reading one file.
3. The model replies with text, or proposes an action: a structured tool call carrying target cells and values as data. **The backend never executes it.**
4. Formula responses pass through a **Reflexion loop** (up to three iterations) that critiques the proposal against the actual spreadsheet state and records the full critique chain.
5. The **pre-write hook** runs the deterministic rules and attaches the results to the proposal. It never blocks — the user, not the system, is the final decision-maker.
6. The task pane renders an **approval card** showing the proposal and the check results together. The user can approve, adopt a rule-suggested revision, reject with a reason (which re-enters generation), or reject outright.
7. Only on approval does the frontend write to the workbook through Office.js. The backend has no write access to Excel at all — the only path from model output to a cell passes through the card the user saw.
8. Every stage emits an event to the append-only audit log, exportable as Markdown from `/audit/view`. Clearing the log writes an `audit_cleared` marker as the first line of the new log.

## Capabilities

| Capability | Notes |
|------------|-------|
| Summarise, Clean, Find outliers, Analyse, Create chart | Quick-action chips, each defined by a skill file |
| Review | On-demand assumption audit over the current selection (hardcoded rates, sanity ranges) |
| Forecast | Deliberately chat-only, so the user supplies the business context that shapes the projection |
| Variance analysis | Income Statement, Balance Sheet and Cash Flow, two-year variance. The model reads only *structure*; deterministic code computes every figure — five accounting-identity tie-out checks and six cross-statement ratios |
| DCF valuation | Derives historical driver ratios from the workbook (with cell-level provenance), then proposes a two-sheet WACC + DCF template whose declared assumptions are verified cell-by-cell by integrity and sanity rules |

## Architecture

```
Excel task pane  (Office.js, vanilla JS — src/taskpane/)
        │  JSON over HTTP (localhost:8000)
        ▼
FastAPI orchestrator  (backend/main.py)
  the audit chokepoint: every server call is wrapped in audit events
        │  stdio JSON-RPC (Model Context Protocol)
        ├── excelcat-verify     deterministic rule checks + Reflexion loop
        ├── excelcat-skills     skill specifications
        └── excelcat-analysis   variance / financial compute
```

Invariants the design maintains:

- **MCP servers are pure compute** — they hold no secrets and cannot emit audit events, so the trail has exactly one writer (`main.py`) by construction, not by convention.
- **The LLM is a replaceable component** behind a model registry (Gemini 2.5 Flash default; DeepSeek and GPT selectable per message). The verification layer is identical whichever model proposes a write, and the audit trail records the resolved model identifier for every run.
- **Action tools are executed by the frontend after approval** — there is deliberately no Excel-writing server in the backend.
- **Fail-loud degradation** — if a server is down, the capability reports itself unavailable rather than silently skipping checks.

## Repository layout

```
backend/
  main.py             FastAPI app + MCP orchestrator; audit chokepoint
  llm_client.py       LLM transport, model registry, canonical tool registry
  audit.py            append-only JSONL audit log
  audit_render.py     audit.jsonl → Markdown renderer
  variance.py, ratios.py, ties.py, dcf.py
                      deterministic financial compute
  mcp_servers/        excelcat-verify / -skills / -analysis (stdio MCP servers)
  rules/              verification rules; rules/financial/ holds DCF/WACC rules
  skills/             Markdown skill specifications — the product contracts
src/
  taskpane/           chat pane UI (vanilla JS modules)
  commands/           Office ribbon commands
manifest.xml          Office add-in manifest
```

## Running locally

Prerequisites: Python 3.11+, Node.js 18+, desktop Excel (Microsoft 365).

**Backend** (port 8000):

```bash
cd backend
pip install fastapi uvicorn python-dotenv google-genai mcp httpx
cp .env.example .env          # then fill in GEMINI_API_KEY (the minimum)
uvicorn main:app --port 8000 --reload
```

**Add-in** (port 3000):

```bash
npm install
npm start                     # builds, serves, and sideloads into desktop Excel
```

`npm start` uses `office-addin-debugging` to sideload `manifest.xml`; the first run may prompt to install local development certificates.

## Status — July 2026

Working end-to-end: chat → intent → skill or action → Reflexion → approval card → execute, with the audit trail covering the full sequence. Variance analysis Phases 1–3 (IS + BS + CF) and DCF valuation are implemented. Next: systematic evaluation against the error categories ICAEW has published for AI-generated financial models.

---

Research prototype built by Chengkai Li as an MSc Computer Science dissertation project (2026). Not production software; nothing here is financial advice.
