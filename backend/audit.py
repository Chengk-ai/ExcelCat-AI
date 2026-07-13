"""
Audit trail — append-only JSONL event log.

Design constraints:
- Append-only. We never rewrite history; older entries stay byte-identical.
- Never throws. The product still works if the disk is full or the file is
  locked; audit is observability, not a critical path. Every public function
  swallows exceptions silently. Failures here must not break /chat.
- User-gated. The frontend sends `audit_enabled` per request; when False we
  short-circuit at the entry of every event call. No file is created until
  the user has opted in at least once.

Companion module `audit_render.py` consumes this file and produces a
human-readable MD view. The JSONL is the source of truth.
"""
from __future__ import annotations
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

# Stored next to backend/main.py — same conventions as skills/.
AUDIT_FILE = os.path.join(os.path.dirname(__file__), "audit.jsonl")


def _utcnow_iso() -> str:
    """ISO-8601 timestamp with millisecond precision and explicit Z suffix."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def append_event(
    event_type: str,
    payload: dict,
    request_id: str,
    enabled: bool = True,
) -> None:
    """
    Append a single event line to audit.jsonl.

    Args:
        event_type: one of chat_request | reflexion_run | hook_check |
                    chat_response | approval_decision
        payload: event-specific dict; serialised verbatim under "payload"
        request_id: UUID-like string tying events from one /chat round
                    together. The renderer groups by this.
        enabled: caller's audit-enabled flag. False short-circuits — nothing
                 is written, no file is created.
    """
    if not enabled:
        return
    try:
        record = {
            "ts": _utcnow_iso(),
            "request_id": request_id,
            "event": event_type,
            "payload": payload,
        }
        # default=str so unexpected non-serialisable types (e.g. Decimal,
        # datetime) degrade to a string instead of raising. The audit log
        # MUST NOT break the request.
        line = json.dumps(record, ensure_ascii=False, default=str)
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        # Never raise from audit. We tolerate full disks, perms, encoding
        # issues — none of them should be visible to the user.
        pass


def clear() -> bool:
    """
    Delete audit.jsonl and the rendered audit.md, if present, then start the
    new log with an `audit_cleared` event as its first line.

    The marker makes a wipe distinguishable from a log that never existed:
    an auditor reading a fresh file that *begins* with audit_cleared knows
    history was removed deliberately (and how many events), rather than
    wondering whether the trail was tampered with. It is only written when
    something was actually removed, so a user who never opted into audit
    still gets no file created.

    Returns True if at least one file was removed, False otherwise.
    Never throws.
    """
    removed = False
    events_removed = 0
    md_file = os.path.join(os.path.dirname(__file__), "audit.md")
    try:
        with open(AUDIT_FILE, "r", encoding="utf-8") as f:
            events_removed = sum(1 for line in f if line.strip())
    except Exception:
        pass
    for path in (AUDIT_FILE, md_file):
        try:
            if os.path.exists(path):
                os.remove(path)
                removed = True
        except Exception:
            pass
    if removed:
        # Deletion and the first-line marker live in the same function so no
        # caller can end up with a fresh log that lacks the marker.
        append_event(
            "audit_cleared",
            {"events_removed": events_removed},
            request_id=str(uuid.uuid4()),
        )
    return removed
