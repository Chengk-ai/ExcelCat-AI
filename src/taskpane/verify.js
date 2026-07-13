// Verification Layer panel: one log entry per tool_call, plus the user's
// decision written back onto the matching entry.

import { state, panelVerify, escapeHtml } from './core';

// ── Verification Layer log ─────────────────────────────
// Appends one entry per tool_call to #verify-log. Newest at the bottom
// (chat-log style). State lives in the DOM — entries persist for the
// page session.
state.verifyCount = state.verifyCount || 0;

// Build a short, human-readable subject for the entry head, e.g.
// "write_to_cell · B5" or "create_chart · column". Falls back to just
// the tool name if no useful arg is present.
function _verifySubject(toolCall) {
  const name = toolCall.name || 'tool_call';
  const args = toolCall.args || {};
  if (name === 'write_to_cell' && args.cell) return `${name} · ${args.cell}`;
  if (name === 'create_chart' && args.chart_type) return `${name} · ${args.chart_type}`;
  if (name === 'apply_formula_pattern' && args.range) {
    const n = Array.isArray(args.cells) ? args.cells.length : 0;
    return `apply_pattern · ${args.range}${n ? ` (${n} cells)` : ''}`;
  }
  if (name === 'apply_cleaning') {
    const n = Array.isArray(args.cells) ? args.cells.length : 0;
    return `apply_cleaning · ${n} cell${n === 1 ? '' : 's'}`;
  }
  if (name === 'apply_forecast') {
    const n = Array.isArray(args.cells) ? args.cells.length : 0;
    return `apply_forecast · ${n} cell${n === 1 ? '' : 's'}`;
  }
  if (name === 'apply_dcf_template') {
    const n = (args.sheets || []).reduce((t, s) => t + ((s.cells || []).length), 0);
    return `apply_dcf_template · ${n} cells → WACC + DCF`;
  }
  return name;
}

// The actual content the tool would write. Shown on every entry so the
// audit trail records WHAT was verified, not just THAT it was verified.
function _verifyPayload(toolCall) {
  const name = toolCall.name;
  const args = toolCall.args || {};
  if (name === 'write_to_cell') return args.value ?? '';
  if (name === 'create_chart')  return args.title ? `${args.chart_type} · "${args.title}"` : args.chart_type ?? '';
  if (name === 'apply_formula_pattern') return args.pattern ?? '';
  if (name === 'apply_cleaning') {
    const fixes = Array.isArray(args.fix_types) ? args.fix_types : [];
    return fixes.length ? `mechanical fixes: ${[...new Set(fixes)].join(', ')}` : '';
  }
  if (name === 'apply_forecast') {
    const rate = typeof args.assumed_growth_rate === 'number'
      ? ` · assumed ${(args.assumed_growth_rate * 100).toFixed(0)}%` : '';
    return `${args.method || 'forecast'}${rate}`;
  }
  if (name === 'apply_dcf_template') {
    const pct = v => (typeof v === 'number' && isFinite(v)) ? `${(v * 100).toFixed(1)}%` : '?';
    return `WACC ${pct(args.wacc)} · TGR ${pct(args.terminal_growth)} · ${args.forecast_years ?? '?'}y forecast`;
  }
  return Object.keys(args).length ? JSON.stringify(args) : '';
}

export function appendVerifyLog(toolCall, approvalId) {
  const log = document.getElementById('verify-log');
  if (!log) return;

  const hook = toolCall.hook_result || {};
  const status = hook.status || 'ok';
  const checks = Array.isArray(hook.checks_run) ? hook.checks_run : [];
  const iterations = hook.review_meta?.iterations;
  const warnings = Array.isArray(hook.warnings) ? hook.warnings : [];
  const suggestions = Array.isArray(hook.suggestions) ? hook.suggestions : [];

  let badgeLabel, badgeClass;
  if (status === 'warning')         { badgeLabel = '⚠️ Warning';    badgeClass = 'warning'; }
  else if (status === 'suggestion') { badgeLabel = '💡 Suggestion'; badgeClass = 'suggestion'; }
  else                              { badgeLabel = '✓ Verified';   badgeClass = 'ok'; }

  // Build the message body. Warnings are listed; suggestions render as
  // an original → suggested diff plus the reason.
  let msgHtml = '';
  if (warnings.length) {
    const items = warnings
      .map(w => typeof w === 'string' ? w : (w.message || JSON.stringify(w)))
      .map(t => `<li>${escapeHtml(t)}</li>`)
      .join('');
    msgHtml = `<ul class="verify-entry-list">${items}</ul>`;
  } else if (suggestions.length) {
    msgHtml = suggestions.map(s => {
      if (typeof s === 'string') return `<div>${escapeHtml(s)}</div>`;
      const original  = s.original  ?? '';
      const suggested = s.suggested ?? s.suggested_value ?? '';
      const reason    = s.reason    ?? s.message ?? '';
      const diff = (original && suggested)
        ? `<div class="verify-entry-diff">
             <code class="verify-diff-old">${escapeHtml(original)}</code>
             <span class="verify-diff-arrow">→</span>
             <code class="verify-diff-new">${escapeHtml(suggested)}</code>
           </div>`
        : '';
      const reasonLine = reason ? `<div class="verify-entry-reason">${escapeHtml(reason)}</div>` : '';
      return diff + reasonLine;
    }).join('');
  }

  const now = new Date();
  const absTime = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

  // Demote the previous newest entry (now second-to-last) from "just now"
  // to its absolute time.
  const entries = log.querySelectorAll('.verify-entry');
  const prevNewest = entries[entries.length - 1];
  if (prevNewest) {
    const prevTimeEl = prevNewest.querySelector('.verify-entry-time');
    if (prevTimeEl && prevTimeEl.dataset.abs) {
      prevTimeEl.textContent = prevTimeEl.dataset.abs;
    }
  }

  const entry = document.createElement('div');
  entry.className = `verify-entry status-${badgeClass}`;
  if (approvalId) entry.dataset.approvalId = approvalId;
  const payload = _verifyPayload(toolCall);
  entry.innerHTML = `
    <div class="verify-entry-head">
      <span class="verify-entry-badge ${badgeClass}">${badgeLabel}</span>
      <span class="verify-entry-tool">${escapeHtml(_verifySubject(toolCall))}</span>
      <span class="verify-entry-time" data-abs="${absTime}" title="${absTime}">just now</span>
    </div>
    ${payload ? `<div class="verify-entry-payload"><code>${escapeHtml(payload)}</code></div>` : ''}
    <div class="verify-entry-meta">
      ${checks.map(c => `<span class="verify-entry-chip">${escapeHtml(c)}</span>`).join('')}
      ${typeof iterations === 'number' ? `<span class="verify-entry-iters">${iterations} iter${iterations === 1 ? '' : 's'}</span>` : ''}
    </div>
    ${msgHtml ? `<div class="verify-entry-msg">${msgHtml}</div>` : ''}
  `;

  // Reveal the panel the first time a check arrives (it's hidden by default
  // so it doesn't take up space before anything happens).
  if (panelVerify) panelVerify.style.display = '';

  log.appendChild(entry);
  // Auto-scroll so the newest entry is visible.
  log.scrollTop = log.scrollHeight;

  state.verifyCount += 1;
  const counter = document.getElementById('verify-count');
  if (counter) counter.textContent = `${state.verifyCount} check${state.verifyCount === 1 ? '' : 's'}`;
}

// Append the user's decision (approved / used-ai / rejected / rejected-retry
// / failed) to the matching verify-log entry. Idempotent — if a decision is
// already shown, replaces it. This is the audit-trail half of the panel:
// hook check + human decision together tell the full story.
export function markVerifyDecision(approvalId, decisionKey, detail) {
  if (!approvalId) return;
  const entry = document.querySelector(`.verify-entry[data-approval-id="${approvalId}"]`);
  if (!entry) return;

  const decisions = {
    'approved':       { label: '✓ Approved',             cls: 'approved' },
    'used-ai':        { label: '✦ Used AI\'s version',   cls: 'used-ai' },
    'rejected':       { label: '✕ Rejected',             cls: 'rejected' },
    'rejected-retry': { label: '↻ Rejected & retried',   cls: 'rejected-retry' },
    'failed':         { label: '⚠️ Execution failed',    cls: 'failed' },
  };
  const d = decisions[decisionKey];
  if (!d) return;

  // Replace any previous decision block on this entry.
  const existing = entry.querySelector('.verify-entry-decision-wrap');
  if (existing) existing.remove();

  const wrap = document.createElement('div');
  wrap.className = 'verify-entry-decision-wrap';

  const badge = document.createElement('div');
  badge.className = `verify-entry-decision ${d.cls}`;
  badge.textContent = d.label;
  wrap.appendChild(badge);

  // Optional detail line — used for the user's retry reason or any other
  // free-form note attached to the decision. Quoted to make it obvious
  // this is the user's words, not ours.
  if (detail) {
    const detailEl = document.createElement('div');
    detailEl.className = `verify-entry-decision-detail ${d.cls}`;
    detailEl.textContent = `“${detail}”`;
    wrap.appendChild(detailEl);
  }

  entry.appendChild(wrap);
}
