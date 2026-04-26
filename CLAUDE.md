# ExcelCat AI

Excel Office Add-in for accountants and financial analysts. Differentiator vs Microsoft Copilot: **verifiability and audit trail**. The product wins by being auditable, not by being more general-purpose.

## Stack

- **Backend**: FastAPI (Python), DeepSeek V4 Flash as primary (cheaper), Gemini 2.5 Flash as fallback. Reflexion critic + reviser also use DeepSeek.
- **Frontend**: Office.js Add-in, vanilla HTML/CSS/JS, webpack + babel build
- **Skills**: markdown files in `backend/skills/` — these are user-facing specs, not internal prompts
- **AI architecture**: DeepSeek primary with Gemini fallback; function calling via both; MCP refactor planned

## Layout

```
.
├── backend/
│   ├── main.py              # FastAPI app: chat endpoint, hooks, reflexion
│   ├── skills/              # Skill specs (markdown, version-controlled)
│   │   ├── summarise.md
│   │   ├── clean_data.md
│   │   ├── find_outliers.md
│   │   └── analyse_data.md
│   └── .env                 # API keys (gitignored)
├── src/
│   ├── taskpane/            # Main UI: chat pane shown inside Excel
│   │   ├── taskpane.html
│   │   ├── taskpane.css
│   │   └── taskpane.js      # state, Office.js calls, approval flow
│   └── commands/            # Office ribbon commands
├── assets/                  # Icons + mascot source images
├── scripts/
│   └── build-icons-from-cat.py
├── manifest.xml             # Office Add-in manifest
├── webpack.config.js
├── package.json
└── CLAUDE.md
```

The taskpane is split into `taskpane.html`, `taskpane.css`, and `taskpane.js`.

## Core architecture decisions

**`pre_write_hook` does not block.** It runs checks on action tool_calls and attaches results as metadata. The frontend approval card decides whether the user proceeds. This is deliberate — it preserves user agency, which is the whole point of "verifiability over automation".

**Skill files are single source of truth.** No hidden prompts in the frontend. If chip behaviour changes, the skill file changes. Auditors can read one file per feature and understand exactly what triggers what.

**JSON-returning tool calls, not WebSocket.** Chosen so the Verification Layer has interception points before any cell is written.

**Reflexion loop runs on every formula response** (up to 3 iterations). Critic checks formulas against actual data state, not just syntax. `analyse_data_state()` exists because the LLM critic ignores nuance unless we put facts in front of it explicitly.

## Don't do

- **Don't auto-overwrite skill files** based on AI proposals. Skill files are product contracts — every change needs human review.
- **Don't add hidden instructions in the frontend** that aren't in the skill files. Breaks the audit story.
- **Don't bypass `pre_write_hook` on action tool_calls.** Even if a check feels redundant, the hook is the single chokepoint for the audit trail.
- **Don't `print()` user data** (e.g. `request.context.values`) in new debug lines. Existing `[DEBUG]` lines are grandfathered.
- **Don't commit `.env`.** It's in `.gitignore` for a reason.
- **Don't change `webpack.config.js`** unless the task explicitly requires it.

## Working with me

- **Plan first, then edit.** For any task that touches more than ~30 lines or multiple files, propose the plan and wait for approval before editing. Show what you'll change and why. After I approve, make the edits and stop — don't roll into the next task uninvited.
- **One task per session.** When the current task is done, stop. I'll start a fresh session for the next thing so context stays clean.
- **Constraints over instructions.** When I give you constraints ("don't change logic", "don't touch X"), they override your sense of what would be "better". If you think a constraint is wrong, say so before editing — don't ignore it.
- **Don't run servers or tests.** I verify manually in Excel. You can run `git diff`, `git status`, `cat`, etc., but don't `npm start`, `pytest`, or anything long-running unless I ask.
- **If you find a real bug while doing something else, mention it but don't fix it.** Surface it, let me decide whether to scope it in.
- **Don't use git worktrees.** Edit files directly in the main working directory.

## How I work

- I'm a vibe coder moving toward more rigorous foundations. Explain *why* something works, not just *how*. Use analogies grounded in this project's architecture.
- I prefer Simplified Mandarin when explanations get complex or unclear. Default to English otherwise.
- Show me the smallest change that solves the problem. If a bigger refactor is right, say so but flag it as a separate decision.
- British English in user-facing text (skill files, UI strings, README).
- Don't apologise reflexively. If I'm wrong, push back.

## Current state (April 2026)

MVP works end-to-end: chat → intent → skill or action → reflexion → approval card → execute. Five quick-action chips integrated with skill files (no hidden prompts). DeepSeek fallback works. Rebranded to ExcelCat AI.

Shipped: audit trail (JSONL append-only log), async DeepSeek (httpx), `_extract_formula` hardening (multi-strategy with paren-depth tracking), batch-write collapse, taskpane split into html/css/js.

## On the horizon

Roadmap (priority order):

1. **Verification Layer expansion** — Data Integrity (debit/credit balance), Financial Logic (WACC sanity, DCF constraints).
2. **MCP refactor** — extract existing tools into MCP servers, then refactor `main.py` into a pure MCP client.

## Glossary

- **Skill**: a markdown file in `backend/skills/` that defines a user-facing capability (summarise, clean, etc.). Loaded by name when its corresponding function call fires.
- **Hook**: `pre_write_hook` — runs on action tool_calls, returns metadata, never blocks.
- **Reflexion**: the critique → revise → re-verify loop in `reflexion_review`.
- **Approval card**: frontend UI that shows the user a proposed tool_call + hook results, with Approve / Use AI's version / Reject & retry / Reject buttons.
- **Chip**: a quick-action button in the task pane header. Clicking populates the chat input with a preset prompt.