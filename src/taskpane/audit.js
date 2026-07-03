// Audit UI helpers + the /audit/decision poster. The menu/toggle event
// wiring itself lives in taskpane.js (entry).

import { state, API_BASE, auditMenu, btnMenu, miAuditToggle } from './core';

// ── Audit overflow menu ────────────────────────────────
// Open/close the ⋯ dropdown. Closes on outside click.
export function setMenu(open) {
  if (!auditMenu || !btnMenu) return;
  auditMenu.hidden = !open;
  btnMenu.setAttribute('aria-expanded', String(open));
}

// ── Audit toggle ───────────────────────────────────────
// Sync the switch visual to current state, then wire click → flip + persist.
export function applyAuditUi() {
  if (!miAuditToggle) return;
  miAuditToggle.classList.toggle('on', state.auditEnabled);
  const tip = state.auditEnabled
    ? 'Audit trail: ON (click to disable)'
    : 'Audit trail: OFF (click to enable)';
  miAuditToggle.setAttribute('aria-label', tip);
}

// Fire-and-forget POST to /audit/decision. Used by every approval-card
// button (approve / use_ai / reject / reject_retry) and by the partial-
// failure path in batch writes. Swallows network errors — audit is not
// allowed to break the chat flow.
export async function postAuditDecision(requestId, toolIndex, decision, reason) {
  if (!requestId) return;          // pre-audit response or unknown request
  try {
    await fetch(`${API_BASE}/audit/decision`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        request_id: requestId,
        tool_index: typeof toolIndex === 'number' ? toolIndex : 0,
        decision,
        reason: reason || null,
        audit_enabled: state.auditEnabled,
      }),
    });
  } catch {
    // Non-fatal.
  }
}
