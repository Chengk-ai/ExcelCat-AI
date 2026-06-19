// ── State ──────────────────────────────────────────────
const state = {
  messages: [],
  selectionContext: null,
  isTyping: false,
  // Audit trail toggle. Persisted in localStorage. Default ON because
  // verifiability is the product's selling point — the user can opt out.
  // Sent with every /chat request and every /audit/decision call.
  auditEnabled: (localStorage.getItem('excelmate.auditEnabled') ?? 'true') === 'true',
};

// ── DOM ────────────────────────────────────────────────
const messagesEl   = document.getElementById('messages');
const emptyState   = document.getElementById('empty-state');
const chatInput    = document.getElementById('chat-input');
const sendBtn      = document.getElementById('send-btn');
const selPill      = document.getElementById('selection-pill');
const selLabel     = document.getElementById('selection-label');
const clearSelBtn  = document.getElementById('clear-sel');
const btnClear     = document.getElementById('btn-clear');
const btnAudit     = document.getElementById('btn-audit');
const btnAuditClear= document.getElementById('btn-audit-clear');
const btnAuditDl   = document.getElementById('btn-audit-download');
const modelSelect  = document.getElementById('model-select');
state.selectedModel = 'deepseek-v4-flash';

if (modelSelect) {
  modelSelect.addEventListener('change', (e) => {
    state.selectedModel = e.target.value || 'deepseek-v4-flash';
  });
}

// ── Audit toggle ───────────────────────────────────────
// Sync the button visual to current state, then wire click → flip + persist.
function applyAuditUi() {
  if (!btnAudit) return;
  // Native `title` removed — we use the CSS .has-tip tooltip (data-tip)
  // so the hover hint sits below the button and never covers other text.
  // aria-label is kept in sync for screen readers.
  const tip = state.auditEnabled
    ? 'Audit trail: ON (click to disable)'
    : 'Audit trail: OFF (click to enable)';
  if (state.auditEnabled) {
    btnAudit.classList.remove('off');
    btnAudit.classList.add('on');
  } else {
    btnAudit.classList.remove('on');
    btnAudit.classList.add('off');
  }
  btnAudit.setAttribute('data-tip', tip);
  btnAudit.setAttribute('aria-label', tip);
}
applyAuditUi();

if (btnAudit) {
  btnAudit.addEventListener('click', () => {
    state.auditEnabled = !state.auditEnabled;
    localStorage.setItem('excelmate.auditEnabled', String(state.auditEnabled));
    applyAuditUi();
  });
}

// Download audit trail as .md file. Backend renders on demand so the
// hot path (/chat) never pays the full-file I/O cost.
if (btnAuditDl) {
  btnAuditDl.addEventListener('click', async () => {
    try {
      const resp = await fetch('http://127.0.0.1:8000/audit/view');
      if (!resp.ok) return;
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'audit.md';
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      // Non-fatal.
    }
  });
}

if (btnAuditClear) {
  btnAuditClear.addEventListener('click', async () => {
    // No confirmation dialog — the audit history is the user's, deleting
    // it is a normal operation. Backend wipes both audit.jsonl and audit.md.
    try {
      await fetch('http://127.0.0.1:8000/audit/clear', { method: 'POST' });
    } catch {
      // Non-fatal: audit clear failures aren't worth interrupting the user.
    }
  });
}

// Fire-and-forget POST to /audit/decision. Used by every approval-card
// button (approve / use_ai / reject / reject_retry) and by the partial-
// failure path in batch writes. Swallows network errors — audit is not
// allowed to break the chat flow.
async function postAuditDecision(requestId, toolIndex, decision, reason) {
  if (!requestId) return;          // pre-audit response or unknown request
  try {
    await fetch('http://127.0.0.1:8000/audit/decision', {
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

// ── Office Init ───────────────────────────────────────
Office.onReady(info => {
  if (info.host === Office.HostType.Excel) {
    console.log('Office.js ready – Excel');
    // Auto-refresh selection whenever user changes it
    Excel.run(async ctx => {
      ctx.workbook.onSelectionChanged.add(() => { refreshSelection(); });
      await ctx.sync();
    }).catch(() => {});

    // Grab whatever is currently highlighted the moment the app opens!
    refreshSelection();
  }
});

// ── Panel collapse/expand ──────────────────────────────
document.querySelectorAll('.panel .panel-header').forEach(header => {
  header.addEventListener('click', () => {
    header.closest('.panel')?.classList.toggle('collapsed');
  });
});

// ── Selection tracking ─────────────────────────────────
async function refreshSelection() {
  try {
    await Excel.run(async ctx => {
      const range = ctx.workbook.getSelectedRange();
      range.load(['address', 'values', 'formulas', 'rowCount', 'columnCount', 'worksheet/name']);
      await ctx.sync();

      const addr     = range.address.includes('!') ? range.address.split('!')[1] : range.address;
      const sheet    = range.worksheet.name;
      const rows     = range.rowCount;
      const cols     = range.columnCount;
      const values   = range.values;
      const formulas = range.formulas;

      state.selectionContext = { address: addr, sheet, values, formulas, rowCount: rows, columnCount: cols };
      if (selPill) {
        selLabel.textContent = `Selection: ${addr} (${rows}×${cols})`;
        selPill.classList.add('visible');
      }
    });
  } catch {
    // Demo / no Office — no-op
  }
}

// ── Auto-resize textarea ───────────────────────────────
chatInput.addEventListener('input', () => {
  chatInput.style.height = 'auto';
  chatInput.style.height = Math.min(chatInput.scrollHeight, 140) + 'px';
});

// ── Send on Enter ──────────────────────────────────────
chatInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    handleSend();
  }
});
sendBtn.addEventListener('click', handleSend);

// ── Quick action chips ─────────────────────────────────
document.querySelectorAll('.chip[data-prompt]').forEach(chip => {
  chip.addEventListener('click', () => {
    chatInput.value = chip.dataset.prompt;
    chatInput.dispatchEvent(new Event('input'));
    chatInput.focus();
  });
});

// Review Assumptions chip — triggers the review endpoint directly, not chat.
const chipReview = document.getElementById('chip-review');
if (chipReview) {
  chipReview.addEventListener('click', () => triggerReview());
}

clearSelBtn.addEventListener('click', () => {
  state.selectionContext = null;
  selPill.classList.remove('visible');
});

// ── Clear chat ─────────────────────────────────────────
btnClear.addEventListener('click', () => {
  state.messages = [];
  state.pendingApprovals = {};
  renderMessages();

  // Reset the Verification Layer panel to match the empty chat state.
  const log = document.getElementById('verify-log');
  if (log) {
    log.querySelectorAll('.verify-entry').forEach(e => e.remove());
    const empty = document.getElementById('verify-empty');
    if (empty) empty.style.display = '';
  }
  state.verifyCount = 0;
  const counter = document.getElementById('verify-count');
  if (counter) counter.textContent = '0 checks';
});

// ── Core send logic ────────────────────────────────────
async function handleSend() {
  const text = chatInput.value.trim();
  if (!text || state.isTyping) return;

  addMessage('user', text);
  chatInput.value = '';
  chatInput.style.height = 'auto';

  await getAIResponse(text);

}

function addMessage(role, content) {
  state.messages.push({ role, content });
  // If this is the first real message, the empty-state element is taking up
  // the messages area — clear it before appending.
  if (messagesEl.contains(emptyState)) {
    messagesEl.removeChild(emptyState);
  }
  // Append the new message directly. This preserves any non-message DOM
  // (e.g. approval cards) that other code has appended to messagesEl.
  // Typing indicator (if present) should stay BELOW new messages, so we
  // briefly remove it, append the message, then re-insert it at the end.
  const typingEl = document.getElementById('typing-indicator');
  if (typingEl) typingEl.remove();
  messagesEl.appendChild(createMessageEl(role, content));
  if (typingEl) messagesEl.appendChild(typingEl);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function showTyping() {
  // Only one indicator at a time.
  if (document.getElementById('typing-indicator')) return;
  const el = createTypingEl();
  el.id = 'typing-indicator';
  messagesEl.appendChild(el);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function hideTyping() {
  const el = document.getElementById('typing-indicator');
  if (el) el.remove();
}

function renderMessages() {
  // Full rebuild from state. Used for: initial render, "Clear chat", and
  // recovery scenarios. NOT called on every addMessage anymore — that
  // would wipe out approval cards and other appended DOM.
  messagesEl.innerHTML = '';

  if (state.messages.length === 0) {
    messagesEl.appendChild(emptyState);
    return;
  }

  state.messages.forEach(msg => {
    messagesEl.appendChild(createMessageEl(msg.role, msg.content));
  });

  if (state.isTyping) {
    showTyping();
  }

  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function createMessageEl(role, content) {
  const wrap = document.createElement('div');
  wrap.className = `message ${role}`;

  const avatar = document.createElement('div');
  avatar.className = `msg-avatar ${role === 'user' ? 'usr' : 'ai'}`;
  avatar.textContent = role === 'user' ? 'U' : 'C';

  const body = document.createElement('div');
  body.className = 'msg-body';

  const roleLabel = document.createElement('div');
  roleLabel.className = `msg-role ${role === 'user' ? 'usr' : 'ai'}`;
  roleLabel.textContent = role === 'user' ? 'You' : 'ExcelCat AI';

  const contentEl = document.createElement('div');
  contentEl.className = 'msg-content';
  contentEl.innerHTML = formatContent(content);

  body.appendChild(roleLabel);
  body.appendChild(contentEl);

  // Action buttons for AI messages
  if (role === 'assistant') {
    const actions = document.createElement('div');
    actions.className = 'msg-actions';
    actions.innerHTML = `
      <button class="msg-action-btn" onclick="copyMsg(this)">
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
          <rect x="5" y="5" width="8" height="8" rx="1.5"/>
          <path d="M3 11V3h8"/>
        </svg>
        Copy
      </button>
    `;
    body.appendChild(actions);
  }

  wrap.appendChild(avatar);
  wrap.appendChild(body);
  return wrap;
}

function createTypingEl() {
  const wrap = document.createElement('div');
  wrap.className = 'message ai typing-indicator';
  wrap.innerHTML = `
    <div class="msg-avatar ai" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M14 9V5a3 3 0 0 0-3-3l-1 4-3 3v11h9a2 2 0 0 0 2-2l1-7a2 2 0 0 0-2-2h-3z"/>
        <path d="M7 22H4a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1h3"/>
      </svg>
    </div>
    <div class="msg-body">
      <div class="msg-role ai">ExcelCat AI</div>
      <div class="msg-content"><div class="dots"><span></span><span></span><span></span></div></div>
    </div>
  `;
  return wrap;
}

function formatContent(text) {
  // Simple markdown-lite: code blocks and inline code
  return text
    .replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}

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
  return Object.keys(args).length ? JSON.stringify(args) : '';
}

function appendVerifyLog(toolCall, approvalId) {
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

  // Hide empty state on first entry.
  const empty = document.getElementById('verify-empty');
  if (empty) empty.style.display = 'none';

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
function markVerifyDecision(approvalId, decisionKey, detail) {
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

// ── Approval flow (Step 2: minimal) ────────────────────
// Tracks pending tool_calls awaiting user decision.
state.pendingApprovals = state.pendingApprovals || {};

function renderApprovalCard(toolCall, approvalId) {
  // approvalId is supplied by the caller so the verify-log entry and the
  // approval card share the same identifier — that lets us update the log
  // entry with the user's decision when they click a button.
  if (!approvalId) {
    approvalId = `appr_${Date.now()}_${Math.random().toString(36).slice(2,7)}`;
  }
  state.pendingApprovals[approvalId] = toolCall;

  const status = toolCall.hook_result?.status || 'ok';
  const statusLabel = { ok: '✓ Verified', suggestion: '💡 Suggestion', warning: '⚠️ Warning' }[status] || status;

  // Describe what the tool will do, in human language.
  let description = '';
  if (toolCall.name === 'write_to_cell') {
    description = `Write <code>${escapeHtml(toolCall.args.value)}</code> to cell <code>${escapeHtml(toolCall.args.cell)}</code>`;
  } else if (toolCall.name === 'create_chart') {
    description = `Create a <code>${escapeHtml(toolCall.args.chart_type)}</code> chart from the selected range`;
  } else if (toolCall.name === 'apply_formula_pattern' || toolCall.name === 'apply_forecast') {
    // Batch write — list every cell→value pair so the audit trail is
    // visible even when the user approves with one click.
    const a = toolCall.args || {};
    const cells = a.cells || [];
    const values = a.values || [];
    const rowsHtml = cells.map((c, k) =>
      `<div class="batch-row"><code class="batch-cell">${escapeHtml(c)}</code><span class="batch-arrow">←</span><code class="batch-value">${escapeHtml(values[k] ?? '')}</code></div>`
    ).join('');
    if (toolCall.name === 'apply_forecast') {
      // Forecast batch — show the method and the audit rationale up top.
      const rangeLabel = a.range || (cells.length ? `${cells[0]}–${cells[cells.length - 1]}` : '');
      description = `
        <div class="batch-summary">
          Forecast <strong>${cells.length} cells</strong> (${escapeHtml(rangeLabel)})
        </div>
        <div class="batch-pattern">
          Method: <code>${escapeHtml(a.method || 'n/a')}</code>
        </div>
        ${a.rationale ? `<div class="batch-pattern">Rationale: ${escapeHtml(a.rationale)}</div>` : ''}
        <div class="batch-rows">${rowsHtml}</div>
      `;
    } else {
      // Pattern shown up top for the "what's the same" answer.
      description = `
        <div class="batch-summary">
          Apply pattern to <strong>${cells.length} cells</strong> (${escapeHtml(a.range || '')})
        </div>
        <div class="batch-pattern">
          Pattern: <code>${escapeHtml(a.pattern || '')}</code>
        </div>
        <div class="batch-rows">${rowsHtml}</div>
      `;
    }
  } else {
    description = `Run <code>${escapeHtml(toolCall.name)}</code>`;
  }

  // Render warnings / suggestions block, if any.
  const warnings = toolCall.hook_result?.warnings || [];
  const suggestions = toolCall.hook_result?.suggestions || [];
  const issuesHtml = renderIssuesBlock(status, warnings, suggestions);

  // Build the action buttons.
  // - suggestion state: 4 buttons (Approve original / Use AI's version / Reject & retry / Reject)
  // - warning/ok state: 3 buttons (Approve / Reject & retry / Reject)
  // - batch (apply_formula_pattern): never offer "Use AI's version" — the
  //   reflexion suggestion is on the sample formula and we'd need symbolic
  //   re-templating to apply it across N rows. Skipped intentionally.
  const isBatch = toolCall.name === 'apply_formula_pattern' || toolCall.name === 'apply_forecast';
  let actionsHtml;
  if (status === 'suggestion' && suggestions.length > 0 && !isBatch) {
    actionsHtml = `
      <button class="approval-btn approve"        onclick="approveToolCall('${approvalId}')">Approve original</button>
      <button class="approval-btn use-suggestion" onclick="useAISuggestion('${approvalId}')">Use AI's version</button>
      <button class="approval-btn reject-retry"   onclick="openRetryForm('${approvalId}')">Reject &amp; retry</button>
      <button class="approval-btn reject"         onclick="rejectToolCall('${approvalId}')">Reject</button>
    `;
  } else {
    const approveLabel = status === 'warning' ? 'Approve anyway' : 'Approve';
    actionsHtml = `
      <button class="approval-btn approve"      onclick="approveToolCall('${approvalId}')">${approveLabel}</button>
      <button class="approval-btn reject-retry" onclick="openRetryForm('${approvalId}')">Reject &amp; retry</button>
      <button class="approval-btn reject"       onclick="rejectToolCall('${approvalId}')">Reject</button>
    `;
  }

  // Build the card as a message in the chat stream.
  const wrap = document.createElement('div');
  wrap.className = 'message ai';
  wrap.dataset.approvalId = approvalId;
  wrap.innerHTML = `
    <div class="msg-avatar ai" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M14 9V5a3 3 0 0 0-3-3l-1 4-3 3v11h9a2 2 0 0 0 2-2l1-7a2 2 0 0 0-2-2h-3z"/>
        <path d="M7 22H4a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1h3"/>
      </svg>
    </div>
    <div class="msg-body">
      <div class="msg-role ai">ExcelCat AI · proposed change</div>
      <div class="approval-card status-${status}">
        <div class="approval-card-head">
          <div class="approval-card-tool">${escapeHtml(toolCall.name)}</div>
          <div class="approval-card-status ${status}">${statusLabel}</div>
        </div>
        <div class="approval-card-body">${description}</div>
        ${issuesHtml}
        <div class="approval-card-actions">
          ${actionsHtml}
        </div>
        <div class="approval-retry-form">
          <label>Tell the AI what was wrong</label>
          <textarea placeholder="e.g. The formula references cells outside the data range. Try again with the actual selection."></textarea>
          <div class="approval-retry-form-actions">
            <button class="approval-btn reject-retry" onclick="submitRetryForm('${approvalId}')">Send &amp; retry</button>
            <button class="approval-btn reject"       onclick="closeRetryForm('${approvalId}')">Cancel</button>
          </div>
        </div>
        <div class="approval-card-resolution"></div>
      </div>
    </div>
  `;
  messagesEl.appendChild(wrap);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

// Render the warnings / suggestions block for an approval card.
// Returns '' (empty string) if there's nothing to show — the card stays clean.
function renderIssuesBlock(status, warnings, suggestions) {
  if (!warnings.length && !suggestions.length) return '';

  // Warnings (hard issues)
  if (warnings.length) {
    const items = warnings.map(w => `<li>${escapeHtml(w)}</li>`).join('');
    return `
      <div class="approval-card-issues warning">
        <div class="approval-card-issues-label">⚠️ Issues found</div>
        <ul>${items}</ul>
      </div>
    `;
  }

  // Suggestions (AI's alternative versions, with diff)
  const items = suggestions.map(s => `
    <li>
      <span class="reason">${escapeHtml(s.reason || 'AI suggests an alternative.')}</span>
      <div class="diff">
        <div class="diff-row">
          <span class="diff-tag original">your version</span>
          <span class="diff-value">${escapeHtml(s.original)}</span>
        </div>
        <div class="diff-row">
          <span class="diff-tag suggested">AI suggests</span>
          <span class="diff-value">${escapeHtml(s.suggested)}</span>
        </div>
      </div>
    </li>
  `).join('');
  return `
    <div class="approval-card-issues suggestion">
      <div class="approval-card-issues-label">💡 AI has a suggestion</div>
      <ul>${items}</ul>
    </div>
  `;
}

async function approveToolCall(approvalId) {
  const tool = state.pendingApprovals[approvalId];
  if (!tool) return;

  // Lock the buttons immediately so double-clicks don't double-execute.
  const card = document.querySelector(`[data-approval-id="${approvalId}"] .approval-card`);
  card.querySelectorAll('.approval-btn').forEach(b => b.disabled = true);

  try {
    if (tool.name === 'write_to_cell') {
      await writeToCellTool(tool.args.cell, tool.args.value);
      markApprovalResolved(approvalId, 'approved', `✓ Wrote ${tool.args.value} to ${tool.args.cell}`, 'approved');
      postAuditDecision(tool.__requestId, tool.__toolIndex, 'approve');
    } else if (tool.name === 'create_chart') {
      await createNativeChart(tool.args.chart_type, tool.args.title || 'AI Generated Chart');
      markApprovalResolved(approvalId, 'approved', `✓ Created ${tool.args.chart_type} chart`, 'approved');
      postAuditDecision(tool.__requestId, tool.__toolIndex, 'approve');
    } else if (tool.name === 'apply_formula_pattern' || tool.name === 'apply_forecast') {
      // Batch write — loop and execute each cell→value. We do NOT roll
      // back on partial failure (Excel doesn't give us a clean tx). The
      // user gets a "Partial: K/N written" status with the failure detail.
      const cells = tool.args.cells || [];
      const values = tool.args.values || [];
      let written = 0;
      let failure = null;
      for (let k = 0; k < cells.length; k++) {
        try {
          await writeToCellTool(cells[k], values[k]);
          written += 1;
        } catch (err) {
          failure = { cell: cells[k], message: err.message };
          break;
        }
      }
      const rangeLabel = tool.args.range || (cells.length ? `${cells[0]}–${cells[cells.length - 1]}` : '');
      if (!failure) {
        markApprovalResolved(approvalId, 'approved',
          `✓ Wrote ${written} cells (${rangeLabel})`, 'approved');
        postAuditDecision(tool.__requestId, tool.__toolIndex, 'approve',
          `wrote ${written} cells in batch`);
      } else {
        markApprovalResolved(approvalId, 'rejected',
          `⚠️ Partial: ${written}/${cells.length} written — failed at ${failure.cell}: ${failure.message}`,
          'failed', `Failed at ${failure.cell}: ${failure.message}`);
        postAuditDecision(tool.__requestId, tool.__toolIndex, 'failed',
          `Partial batch: ${written}/${cells.length} written; failed at ${failure.cell}: ${failure.message}`);
      }
    }
  } catch (err) {
    markApprovalResolved(approvalId, 'rejected', `⚠️ Failed: ${err.message}`, 'failed');
    postAuditDecision(tool.__requestId, tool.__toolIndex, 'failed', err.message);
  }
  delete state.pendingApprovals[approvalId];
}

// User accepts AI's suggested alternative instead of their original.
// Only available when the hook returned a suggestion for this tool_call.
async function useAISuggestion(approvalId) {
  const tool = state.pendingApprovals[approvalId];
  if (!tool) return;

  const suggestions = tool.hook_result?.suggestions || [];
  // Currently the hook only emits suggestions on `value` for write_to_cell,
  // so we just take the first one. If we add more suggestion fields later,
  // we'd loop and apply each by `field`.
  const suggestion = suggestions.find(s => s.field === 'value');
  if (!suggestion) {
    markApprovalResolved(approvalId, 'rejected', '⚠️ No AI suggestion available', 'failed');
    return;
  }

  const card = document.querySelector(`[data-approval-id="${approvalId}"] .approval-card`);
  card.querySelectorAll('.approval-btn').forEach(b => b.disabled = true);

  try {
    if (tool.name === 'write_to_cell') {
      await writeToCellTool(tool.args.cell, suggestion.suggested);
      markApprovalResolved(approvalId, 'approved',
        `✓ Wrote AI's version (${suggestion.suggested}) to ${tool.args.cell}`, 'used-ai');
      postAuditDecision(tool.__requestId, tool.__toolIndex, 'use_ai',
        `accepted suggestion: ${suggestion.suggested}`);
    }
  } catch (err) {
    markApprovalResolved(approvalId, 'rejected', `⚠️ Failed: ${err.message}`, 'failed');
    postAuditDecision(tool.__requestId, tool.__toolIndex, 'failed', err.message);
  }
  delete state.pendingApprovals[approvalId];
}

function rejectToolCall(approvalId) {
  const tool = state.pendingApprovals[approvalId];
  // Plain reject: no further action, no AI retry.
  markApprovalResolved(approvalId, 'rejected', 'Rejected — no changes made', 'rejected');
  if (tool) postAuditDecision(tool.__requestId, tool.__toolIndex, 'reject');
  delete state.pendingApprovals[approvalId];
}

// ── Reject & retry flow ──────────────────────────────
// Click "Reject & retry" → expand a textarea → user explains why → submit
// posts the rejection as a new user message, which the backend treats as a
// fresh turn with full conversation context.

function openRetryForm(approvalId) {
  const card = document.querySelector(`[data-approval-id="${approvalId}"] .approval-card`);
  if (!card) return;
  const form = card.querySelector('.approval-retry-form');
  // Hide the main action buttons while the form is open, to focus attention.
  card.querySelector('.approval-card-actions').style.display = 'none';
  form.classList.add('open');
  form.querySelector('textarea').focus();
}

function closeRetryForm(approvalId) {
  const card = document.querySelector(`[data-approval-id="${approvalId}"] .approval-card`);
  if (!card) return;
  const form = card.querySelector('.approval-retry-form');
  form.classList.remove('open');
  card.querySelector('.approval-card-actions').style.display = 'flex';
  form.querySelector('textarea').value = '';
}

async function submitRetryForm(approvalId) {
  const tool = state.pendingApprovals[approvalId];
  if (!tool) return;

  const card = document.querySelector(`[data-approval-id="${approvalId}"] .approval-card`);
  const form = card.querySelector('.approval-retry-form');
  const reason = form.querySelector('textarea').value.trim();

  if (!reason) {
    form.querySelector('textarea').focus();
    return;
  }

  // Lock the original card and show the rejection in its resolution slot.
  // Use a forced state so reject styling wins even though we resolved via retry.
  markApprovalResolved(approvalId, 'rejected', `Rejected & asked AI to retry — "${reason}"`, 'rejected-retry', reason);
  postAuditDecision(tool.__requestId, tool.__toolIndex, 'reject_retry', reason);
  delete state.pendingApprovals[approvalId];

  // Build a structured retry message. Keeping it readable so the user can see
  // their own rejection in the chat history (audit trail), and Gemini gets
  // clear context about what was rejected and why.
  // The closing instruction is action-oriented to ensure Gemini emits a new
  // tool_call instead of treating the rejection as a conversation prompt.
  const proposalSummary = describeToolCall(tool);
  const retryMessage =
    `[Previous proposal was rejected — please retry]\n` +
    `Original tool call: ${proposalSummary}\n` +
    `Reason for rejection: ${reason}\n\n` +
    `Generate a new proposal that addresses the feedback. ` +
    `Call the appropriate tool (e.g. write_to_cell) directly with corrected arguments. ` +
    `Do not ask clarifying questions — make a concrete new proposal.`;

  // Display the retry as a normal user message, then go through the standard
  // AI response path. The backend already handles conversation context, so
  // no backend change is needed.
  addMessage('user', retryMessage);
  await getAIResponse(retryMessage);
}

// Compact human-readable summary of a tool_call, used in retry messages.
function describeToolCall(tool) {
  if (tool.name === 'write_to_cell') {
    return `write_to_cell(cell="${tool.args.cell}", value="${tool.args.value}")`;
  }
  if (tool.name === 'create_chart') {
    return `create_chart(chart_type="${tool.args.chart_type}")`;
  }
  return `${tool.name}(${JSON.stringify(tool.args)})`;
}

function markApprovalResolved(approvalId, kind, message, decisionKey, decisionDetail) {
  const card = document.querySelector(`[data-approval-id="${approvalId}"] .approval-card`);
  if (!card) return;
  card.classList.add('resolved');
  const resolution = card.querySelector('.approval-card-resolution');
  resolution.classList.add(kind);
  resolution.textContent = message;
  // Mirror the decision into the Verification Layer log entry, if linked.
  if (decisionKey) markVerifyDecision(approvalId, decisionKey, decisionDetail);
}

function escapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// ── AI response ──────────────
async function getAIResponse(userText) {
  state.isTyping = true;
  sendBtn.disabled = true;
  showTyping();

  // 90s timeout — reflexion can run up to 5 LLM calls sequentially.
  // Without this, a hung backend leaves the typing indicator spinning
  // forever with no way for the user to recover.
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 90_000);

  try {
    const response = await fetch('http://127.0.0.1:8000/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: userText,
        context: state.selectionContext,
        model: state.selectedModel,
        audit_enabled: state.auditEnabled,
      }),
      signal: controller.signal,
    });
    clearTimeout(timeout);

    if (!response.ok) throw new Error(`Server error: ${response.status}`);

    const data = await response.json();

    state.isTyping = false;
    sendBtn.disabled = false;
    hideTyping();

    if (data.tool_calls && data.tool_calls.length > 0) {
      // If AI also has any text reply, show it first as context.
      if (data.reply && data.reply.trim()) {
        addMessage('assistant', data.reply);
      }

      // Render each tool_call as an approval card. Nothing executes
      // until the user clicks Approve.
      data.tool_calls.forEach((tool, toolIndex) => {
        // Share one id between the verify-log entry and the approval card
        // so user decisions can be written back to the log.
        const approvalId = `appr_${Date.now()}_${Math.random().toString(36).slice(2,7)}`;
        // Tag the tool with the audit identifiers so the button handlers
        // can post a matching approval_decision event later. tool_index is
        // the position of this tool_call in the response — used by the
        // renderer to disambiguate when one /chat returns multiple tools.
        tool.__requestId = data.request_id || null;
        tool.__toolIndex = toolIndex;
        appendVerifyLog(tool, approvalId);
        renderApprovalCard(tool, approvalId);
      });
      return;
    }
    addMessage('assistant', data.reply);

  } catch (err) {
    clearTimeout(timeout);
    state.isTyping = false;
    sendBtn.disabled = false;
    hideTyping();
    const msg = err.name === 'AbortError'
      ? '⚠️ Request timed out (90s). The backend may be overloaded or unreachable.'
      : `⚠️ Error connecting to local backend. Make sure FastAPI is running.\n\n\`${err.message}\``;
    addMessage('assistant', msg);
  }
}

// Write the Chart Function
async function createNativeChart(chartType, title = 'AI Generated Chart') {
  // Throws on failure — caller (approve handler) shows the resolution.
  await Excel.run(async (ctx) => {
    const sheet = ctx.workbook.worksheets.getActiveWorksheet();
    const range = ctx.workbook.getSelectedRange();
    const chart = sheet.charts.add(chartType, range, Excel.ChartSeriesBy.columns);
    chart.title.text = title;
    chart.format.fill.setSolidColor("#ffffff");
    await ctx.sync();
  });
}

// Write to Cell Function
async function writeToCellTool(cellAddress, value) {
  // Throws on failure — caller (approve handler) shows the resolution.
  await Excel.run(async (ctx) => {
    const sheet = ctx.workbook.worksheets.getActiveWorksheet();
    const cell = sheet.getRange(cellAddress);
    cell.values = [[value]];
    await ctx.sync();
  });
}


// ── Review Layer (on-demand assumption checks) ────────
async function triggerReview() {
  if (!state.selectionContext) {
    addMessage('assistant', 'Please select a range first, then click Review Assumptions.');
    return;
  }

  // Show typing while we fetch.
  state.isTyping = true;
  sendBtn.disabled = true;
  showTyping();

  try {
    const resp = await fetch('http://127.0.0.1:8000/review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        values: state.selectionContext.values,
        formulas: state.selectionContext.formulas,
        address: `${state.selectionContext.sheet}!${state.selectionContext.address}`,
        audit_enabled: state.auditEnabled,
      }),
    });

    state.isTyping = false;
    sendBtn.disabled = false;
    hideTyping();

    if (!resp.ok) throw new Error(`Server error: ${resp.status}`);

    const data = await resp.json();
    renderReviewReport(data);
  } catch (err) {
    state.isTyping = false;
    sendBtn.disabled = false;
    hideTyping();
    addMessage('assistant', `⚠️ Review failed: ${err.message}`);
  }
}

function renderReviewReport(data) {
  const results = data.results || [];
  const located = data.located || {};
  const summary = data.summary || '';

  // Remove empty state if still showing.
  if (messagesEl.contains(emptyState)) {
    messagesEl.removeChild(emptyState);
  }

  const locatedEntries = Object.entries(located);
  const inspectedCells = data.inspected_cells || {};
  const rowChips = [];
  for (const kind of ['formula', 'hardcode']) {
    const n = inspectedCells[kind] || 0;
    if (n > 0) rowChips.push(`${n} ${kind} cell${n !== 1 ? 's' : ''}`);
  }
  const inspectedAnything = locatedEntries.length > 0 || rowChips.length > 0;
  const hasIssues = results.length > 0;

  // Three-way badge: findings → orange; inspected-but-clean → green;
  // nothing inspected → neutral. v2 widens "inspected" to include row scans,
  // not just param locator hits, so a clean trend row still counts as
  // "All clear" instead of being mislabelled "Nothing to review".
  let badgeClass, badgeLabel;
  if (hasIssues) {
    badgeClass = 'has-issues';
    badgeLabel = `${results.length} finding${results.length > 1 ? 's' : ''}`;
  } else if (inspectedAnything) {
    badgeClass = 'clean';
    badgeLabel = '✓ All clear';
  } else {
    badgeClass = 'none';
    badgeLabel = 'Nothing to review';
  }

  const paramChipsHtml = locatedEntries
    .map(([k, entries]) => {
      const vals = entries.map(e =>
        e.cell ? `${escapeHtml(e.value)} at ${escapeHtml(e.cell)}` : escapeHtml(e.value)
      ).join(', ');
      return `<span class="review-checked-param">${escapeHtml(k)} = ${vals}</span>`;
    });
  const rowChipsHtml = rowChips
    .map(label => `<span class="review-checked-param">${escapeHtml(label)} scanned</span>`);
  const allChips = [...paramChipsHtml, ...rowChipsHtml];
  const checkedHtml = allChips.length
    ? `<div class="review-report-checked">Checked ${allChips.join('')}</div>`
    : '';

  const itemsHtml = results.map(r => {
    const levelLabel = r.level === 'warning' ? '⚠️ Warning' : '💡 Suggestion';
    return `
      <div class="review-report-item ${r.level}">
        <div class="review-report-item-label">${levelLabel}</div>
        <div class="review-report-item-msg">${escapeHtml(r.message)}</div>
      </div>
    `;
  }).join('');

  const bodyHtml = (checkedHtml || itemsHtml)
    ? `<div class="review-report-body">${checkedHtml}${itemsHtml}</div>`
    : '';

  const wrap = document.createElement('div');
  wrap.className = 'message ai';
  wrap.innerHTML = `
    <div class="msg-avatar ai" aria-hidden="true">C</div>
    <div class="msg-body">
      <div class="msg-role ai">ExcelCat AI · assumption review</div>
      <div class="review-report">
        <div class="review-report-head">
          <div class="review-report-title">Review Assumptions</div>
          <div class="review-report-badge ${badgeClass}">${badgeLabel}</div>
        </div>
        ${bodyHtml}
        <div class="review-report-summary">${escapeHtml(summary)}</div>
      </div>
    </div>
  `;
  messagesEl.appendChild(wrap);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

// ── Expose inline-onclick handlers on window ───────────
// Webpack wraps this file in a module scope, so functions referenced from
// inline `onclick="..."` attributes must be attached to `window` explicitly.
window.approveToolCall = approveToolCall;
window.useAISuggestion = useAISuggestion;
window.rejectToolCall  = rejectToolCall;
window.openRetryForm   = openRetryForm;
window.closeRetryForm  = closeRetryForm;
window.submitRetryForm = submitRetryForm;
window.copyMsg         = copyMsg;

// ── Utility actions ────────────────────────────────────
function copyMsg(btn) {
  const content = btn.closest('.msg-body').querySelector('.msg-content').innerText;
  navigator.clipboard.writeText(content).then(() => {
    btn.textContent = '✓ Copied';
    setTimeout(() => { btn.innerHTML = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="5" y="5" width="8" height="8" rx="1.5"/><path d="M3 11V3h8"/></svg> Copy`; }, 1500);
  });
}
