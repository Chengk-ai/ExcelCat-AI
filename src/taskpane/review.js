// Review chip flow: selection → /review → assumption-review report card.

import { state, API_BASE, messagesEl, escapeHtml, setSendBusy, showToast } from './core';
import { addMessage, showTyping, hideTyping, hideEmptyState, scrollMessages } from './messages';

// Review is deterministic (no LLM) so it's normally quick; the later
// stages only ever show when the backend is struggling.
const REVIEW_STAGES = [
  [0,  'Locating model inputs…'],
  [6,  'Checking inputs against sensible ranges…'],
  [20, 'Still working…'],
];

// ── Review Layer (on-demand assumption checks) ────────
export async function triggerReview() {
  if (!state.selectionContext) {
    // Pre-condition nag — a toast, not a chat message: it shouldn't enter
    // the history (or kill the empty-state launcher).
    showToast('Select the range that holds your model first, then run Review again.');
    return;
  }
  // Truncated context carries no values — reviewing it would silently
  // check nothing and report a clean bill of health.
  if (state.selectionContext.tooLarge) {
    showToast('Selection is too large to review — select just the model block and try again.');
    return;
  }
  if (state.isTyping) {
    showToast('Hold on — another request is still running.');
    return;
  }

  setSendBusy(true);
  showTyping(REVIEW_STAGES);

  // 45s timeout — review is deterministic (no LLM), so anything this slow
  // means the backend is wedged; surface an error rather than leaving the
  // typing indicator spinning forever.
  const controller = new AbortController();
  state.activeController = controller;
  state.stopRequested = false;
  const timer = setTimeout(() => controller.abort(), 45_000);

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
      signal: controller.signal,
    });

    if (!resp.ok) throw new Error(`Server error: ${resp.status}`);

    const data = await resp.json();
    hideTyping();
    renderReviewReport(data);
  } catch (err) {
    hideTyping();
    let msg;
    if (err.name === 'AbortError' && state.stopRequested) {
      msg = '⏹️ Stopped — the review was cancelled.';
    } else if (err.name === 'AbortError') {
      msg = "⚠️ Review timed out after 45 seconds — the backend looks unresponsive. Check it's running, then run Review again.";
    } else {
      msg = `⚠️ Review failed: ${err.message}`;
    }
    addMessage('assistant', msg);
  } finally {
    clearTimeout(timer);
    state.activeController = null;
    setSendBusy(false);
    hideTyping();
  }
}

function renderReviewReport(data) {
  const results = data.results || [];
  const located = data.located || {};
  const summary = data.summary || '';

  // Remove empty state if still showing (also reveals the qa-bar).
  hideEmptyState();

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
      const vals = entries.map(e => {
        let s = e.cell ? `${escapeHtml(e.value)} at ${escapeHtml(e.cell)}` : escapeHtml(e.value);
        // Provenance marker (audit story: where did this number come from?).
        // The backend sends null when the formulas grid couldn't say —
        // definite answers only, no guessing.
        if (e.hardcoded === true) s += ' · hardcoded';
        else if (e.hardcoded === false) s += ' · formula';
        return s;
      }).join(', ');
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
  scrollMessages();
}
