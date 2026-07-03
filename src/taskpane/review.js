// Review chip flow: selection → /review → assumption-review report card.

import { state, API_BASE, messagesEl, emptyState, sendBtn, escapeHtml } from './core';
import { addMessage, showTyping, hideTyping } from './messages';

// ── Review Layer (on-demand assumption checks) ────────
export async function triggerReview() {
  if (!state.selectionContext) {
    addMessage('assistant', 'Please select a range first, then click Review Assumptions.');
    return;
  }

  // Show typing while we fetch.
  state.isTyping = true;
  sendBtn.disabled = true;
  showTyping();

  try {
    const resp = await fetch(`${API_BASE}/review`, {
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
    <div class="msg-avatar ai" aria-hidden="true"><img src="../../assets/cat-head.png" alt="" /></div>
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
