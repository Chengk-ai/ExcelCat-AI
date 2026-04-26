"""
Audit trail renderer — consumes audit.jsonl, produces audit.md.

The JSONL is the source of truth; this module is purely derivative. It can be
deleted at any time and regenerated. Idempotent: running regenerate() twice
produces byte-identical output (modulo the "Generated at" header).

Design choices:
- Read everything into memory each call. The file grows slowly, and keeping
  state across calls (incremental append) would force us to detect external
  edits. Simplicity wins until we have data showing it's a problem.
- Group events by request_id, preserving the order they first appear in the
  file. Within a group, events are kept in file order — they're already in
  the order they happened.
- Each event renders as: a human-readable section + a `<details>` with raw
  JSON. The MD is for skim-reading; the raw block is for precise audit.
- Never throws. Same rationale as audit.py: rendering must not break /chat.
"""
from __future__ import annotations
import json
import os
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Optional

AUDIT_FILE = os.path.join(os.path.dirname(__file__), "audit.jsonl")
MD_FILE = os.path.join(os.path.dirname(__file__), "audit.md")


# ── Public ───────────────────────────────────────────────────────────────────
def regenerate() -> None:
    """Read audit.jsonl, write audit.md. Never raises."""
    try:
        events = _load_events()
        md = _render(events)
        with open(MD_FILE, "w", encoding="utf-8") as f:
            f.write(md)
    except Exception:
        # Audit must never break /chat.
        pass


# ── Internals ────────────────────────────────────────────────────────────────
def _load_events() -> list[dict]:
    """Read JSONL, skip malformed lines."""
    if not os.path.exists(AUDIT_FILE):
        return []
    events: list[dict] = []
    with open(AUDIT_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                # Malformed line — skip rather than fail the whole render.
                continue
    return events


def _render(events: list[dict]) -> str:
    """Compose the full audit.md content."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    parts: list[str] = []
    parts.append("# Audit Trail")
    parts.append("")
    parts.append(f"_Generated {now} · {len(events)} events_")
    parts.append("")
    parts.append(
        "Each section below corresponds to one `/chat` request. Events are "
        "ordered as they happened. Click **raw** to see the underlying JSON."
    )
    parts.append("")

    if not events:
        parts.append("---")
        parts.append("")
        parts.append("_No events yet. Audit trail is enabled but the user hasn't made a request._")
        return "\n".join(parts) + "\n"

    # Group by request_id, preserving first-seen order.
    groups: "OrderedDict[str, list[dict]]" = OrderedDict()
    for e in events:
        rid = e.get("request_id") or "unknown"
        groups.setdefault(rid, []).append(e)

    # Render newest first — easier to skim during demos.
    for rid in reversed(list(groups.keys())):
        parts.append(_render_request(rid, groups[rid]))

    return "\n".join(parts) + "\n"


def _render_request(rid: str, events: list[dict]) -> str:
    """One markdown section for one /chat request."""
    short_id = (rid or "unknown")[:8]
    # Header timestamp = first event's ts.
    first_ts = events[0].get("ts", "") if events else ""

    # Find the chat_request event for the heading subtitle.
    req_ev = next((e for e in events if e.get("event") == "chat_request"), None)
    user_msg = ""
    if req_ev:
        user_msg = (req_ev.get("payload") or {}).get("user_message", "") or ""

    lines: list[str] = []
    lines.append("---")
    lines.append("")
    lines.append(f"## Request `{short_id}` — {first_ts}")
    lines.append("")
    if user_msg:
        # First line of user message as the subtitle, full version inside the event below.
        first_line = user_msg.splitlines()[0] if user_msg else ""
        if len(first_line) > 120:
            first_line = first_line[:117] + "..."
        lines.append(f"_User_: {first_line}")
        lines.append("")

    for e in events:
        lines.append(_render_event(e))
        lines.append("")

    return "\n".join(lines)


def _render_event(e: dict) -> str:
    """One block per event: heading + human summary + raw JSON details."""
    ev = e.get("event", "?")
    ts = e.get("ts", "")
    payload = e.get("payload") or {}

    heading_label, body = _summarise(ev, payload)

    raw_json = json.dumps(e, ensure_ascii=False, indent=2, default=str)
    raw_block = (
        "<details><summary>raw</summary>\n\n"
        f"```json\n{raw_json}\n```\n"
        "</details>"
    )

    parts = [f"### {ts} — {heading_label}", "", body, "", raw_block]
    return "\n".join(parts)


def _summarise(event_type: str, p: dict) -> tuple[str, str]:
    """
    Return (heading, body_md) for a given event. Each branch knows the
    payload shape its writer in main.py uses — keep these in sync.
    """
    if event_type == "chat_request":
        model = p.get("model", "?")
        msg = p.get("user_message", "") or ""
        sel = p.get("selection") or {}
        addr = sel.get("address", "")
        rows = sel.get("rowCount", "?")
        cols = sel.get("columnCount", "?")
        sheet = sel.get("sheet", "")
        body_lines = [
            f"**Chat request** · model: `{model}`",
            "",
            "**User message**:",
            "",
            "```",
            msg,
            "```",
        ]
        if addr:
            body_lines += [
                "",
                f"**Selection**: `{addr}` on `{sheet}` ({rows}×{cols})",
            ]
            values = sel.get("values")
            if values:
                # Render up to 10×10 to keep the MD readable.
                preview_rows = values[:10]
                preview = "\n".join(
                    ", ".join(str(v) for v in row[:10]) for row in preview_rows
                )
                body_lines += ["", "```", preview, "```"]
        return ("Chat request", "\n".join(body_lines))

    if event_type == "reflexion_run":
        verified = p.get("verified")
        iters = p.get("iterations", "?")
        verdict = "✓ verified" if verified else "✗ NOT verified"
        body_lines = [
            f"**Reflexion** · {verdict} · {iters} iteration(s)",
            "",
            f"**Original**: `{p.get('original', '')}`",
            "",
            f"**Final**: `{p.get('final_reply', '')}`",
        ]
        log = p.get("log") or []
        if log:
            body_lines += ["", "**Critique chain**:"]
            for entry in log:
                it = entry.get("iteration", "?")
                crit = (entry.get("critique") or "").strip()
                if len(crit) > 300:
                    crit = crit[:297] + "..."
                revised = " (revised)" if entry.get("revised") else ""
                body_lines.append(f"- iter {it}{revised}: {crit}")
        return (f"Reflexion ({verdict})", "\n".join(body_lines))

    if event_type == "hook_check":
        tool = p.get("tool_name", "?")
        status = p.get("status", "?")
        args = p.get("args") or {}
        warnings = p.get("warnings") or []
        suggestions = p.get("suggestions") or []
        checks = p.get("checks_run") or []
        status_glyph = {"ok": "✓", "suggestion": "💡", "warning": "⚠️"}.get(status, "•")
        body_lines = [
            f"**Hook check** on `{tool}` · status: **{status_glyph} {status}**",
            "",
            f"**Args**: `{json.dumps(args, ensure_ascii=False)}`",
        ]
        if checks:
            body_lines += ["", f"**Checks run**: {', '.join(f'`{c}`' for c in checks)}"]
        if warnings:
            body_lines += ["", "**Warnings**:"]
            for w in warnings:
                body_lines.append(f"- ⚠️ {w}")
        if suggestions:
            body_lines += ["", "**Suggestions**:"]
            for s in suggestions:
                orig = s.get("original", "")
                sug = s.get("suggested", "")
                reason = s.get("reason", "")
                body_lines.append(f"- `{orig}` → `{sug}`")
                if reason:
                    body_lines.append(f"  - _reason_: {reason}")
        return (f"Hook check ({status})", "\n".join(body_lines))

    if event_type == "chat_response":
        skill = p.get("skill_used")
        tcs = p.get("tool_calls_proposed") or []
        excerpt = p.get("reply_excerpt", "") or ""
        if tcs:
            names = ", ".join(f"`{t.get('name','?')}`" for t in tcs)
            body = f"**Response**: proposed {len(tcs)} tool call(s) — {names}"
        elif skill:
            body = "**Response**: skill output (text only)"
        else:
            body = "**Response**: text reply"
        if excerpt:
            body += f"\n\n```\n{excerpt}\n```"
        return ("Chat response", body)

    if event_type == "approval_decision":
        decision = p.get("decision", "?")
        idx = p.get("tool_index", "?")
        reason = p.get("reason") or ""
        glyph = {
            "approve": "✓",
            "use_ai": "✦",
            "reject": "✕",
            "reject_retry": "↻",
            "failed": "⚠️",
        }.get(decision, "•")
        body = f"**User decision** on tool #{idx}: **{glyph} {decision}**"
        if reason:
            body += f"\n\n_Reason_: {reason}"
        return (f"User decision ({decision})", body)

    # Unknown event — fall back to a generic dump.
    return (event_type, f"_(unrecognised event type: {event_type})_")
