// Variance chip flow: find the Income Statement sheet → materiality
// prompt → /variance → variance report card.

import { state, API_BASE, messagesEl, emptyState, sendBtn, escapeHtml } from './core';
import { addMessage, showTyping, hideTyping } from './messages';
import { readSheetByName, IS_SHEET_NAMES } from './excel';

// ── Variance Analysis ──────────────────────────────────
export async function triggerVariance() {
  let sheet = null;
  try {
    sheet = await readSheetByName(IS_SHEET_NAMES);
  } catch {
    sheet = null;
  }
  if (!sheet) {
    addMessage('assistant', 'I couldn’t find an Income Statement tab. Rename the relevant sheet to "IS" or "Income Statement" and try again.');
    return;
  }
  // Establish materiality first (audit discipline): ask for the clearly-trivial
  // threshold, pre-filled with a suggestion, then run once the user confirms.
  const suggested = suggestTrivialThreshold(sheet.values);
  promptMateriality(suggested, sheet.name, trivial => runVariance(sheet, trivial));
}

// Heuristic suggested threshold ≈ 1% of the largest figure in the sheet (a
// revenue proxy), rounded to one significant figure. Only a suggestion — the
// user can overwrite it in the prompt.
function suggestTrivialThreshold(values) {
  let max = 0;
  for (const row of values || []) {
    for (const v of row) {
      if (typeof v === 'number' && isFinite(v)) {
        const a = Math.abs(v);
        if (a > max) max = a;
      }
    }
  }
  if (max <= 0) return 0;
  const raw = max * 0.01;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  return Math.round(raw / mag) * mag;
}

// Inline prompt card: a number input pre-filled with `suggested` + a Run button.
// We avoid window.prompt() — it's unreliable in the Office webview. onRun fires
// once, when the user clicks Run (or presses Enter).
function promptMateriality(suggested, sheetName, onRun) {
  if (messagesEl.contains(emptyState)) messagesEl.removeChild(emptyState);

  const wrap = document.createElement('div');
  wrap.className = 'message ai';
  wrap.innerHTML = `
    <div class="msg-avatar ai" aria-hidden="true"><img src="../../assets/cat-head.png" alt="" /></div>
    <div class="msg-body">
      <div class="msg-role ai">ExcelCat AI · materiality</div>
      <div class="materiality-prompt">
        <div class="mat-title">Set clearly-trivial threshold${sheetName ? ' · ' + escapeHtml(sheetName) : ''}</div>
        <div class="mat-help">Line items whose absolute change is smaller than this are treated as trivial and excluded. In the sheet’s own units; suggested ≈ 1% of the largest figure.</div>
        <div class="mat-row">
          <input type="number" class="mat-input" value="${suggested}" min="0" step="any" />
          <button class="mat-run" type="button">Run analysis</button>
        </div>
      </div>
    </div>`;

  const input = wrap.querySelector('.mat-input');
  const btn = wrap.querySelector('.mat-run');
  const go = () => {
    const v = parseFloat(input.value);
    const threshold = (isFinite(v) && v > 0) ? v : 0;
    btn.disabled = true;
    input.disabled = true;
    btn.textContent = threshold > 0 ? `Running (trivial < ${threshold.toLocaleString('en-GB')})` : 'Running…';
    onRun(threshold);
  };
  btn.addEventListener('click', go);
  input.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); go(); } });

  messagesEl.appendChild(wrap);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  input.focus();
  input.select();
}

async function runVariance(sheet, clearlyTrivial) {
  state.isTyping = true;
  sendBtn.disabled = true;
  showTyping();
  const stop = () => { state.isTyping = false; sendBtn.disabled = false; hideTyping(); };

  try {
    const resp = await fetch(`${API_BASE}/variance`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        values: sheet.values,
        formulas: sheet.formulas,
        address: `${sheet.name}!${sheet.address}`,
        sheet: sheet.name,
        clearly_trivial: clearlyTrivial,
        model: state.selectedModel,
        audit_enabled: state.auditEnabled,
      }),
    });

    stop();
    if (!resp.ok) throw new Error(`Server error: ${resp.status}`);
    const data = await resp.json();
    renderVarianceReport(data, sheet.name);
  } catch (err) {
    stop();
    addMessage('assistant', `⚠️ Variance analysis failed: ${err.message}`);
  }
}

function renderVarianceReport(data, sheetName) {
  const table = data.variance_table || [];
  const anomalies = data.anomalies || [];
  const questions = data.cfo_questions || [];
  const summary = data.summary || '';
  const curLabel = data.current_label || 'Current';
  const priorLabel = data.prior_label || 'Prior';

  if (messagesEl.contains(emptyState)) messagesEl.removeChild(emptyState);

  const fmtNum = n => (typeof n === 'number' && isFinite(n))
    ? n.toLocaleString('en-GB', { maximumFractionDigits: 2 }) : '—';
  const fmtPct = p => (typeof p === 'number' && isFinite(p))
    ? `${p >= 0 ? '+' : ''}${(p * 100).toFixed(1)}%` : 'n/a';

  // Badge: anomalies → orange; computed-but-clean → green; nothing → neutral.
  let badgeClass, badgeLabel;
  if (anomalies.length) {
    badgeClass = 'has-issues';
    badgeLabel = `${anomalies.length} anomal${anomalies.length > 1 ? 'ies' : 'y'}`;
  } else if (table.length) {
    badgeClass = 'clean';
    badgeLabel = '✓ No anomalies';
  } else {
    badgeClass = 'none';
    badgeLabel = 'Nothing to analyse';
  }

  const rowsHtml = table.map(r => {
    const neg = (typeof r.abs_delta === 'number' && r.abs_delta < 0);
    const dir = neg ? 'neg' : 'pos';
    const noBase = r.flags && r.flags.includes('no_prior_base');
    const pctCell = noBase ? 'new' : fmtPct(r.pct_delta);
    const tag = r.trivial ? ' <span class="vt-tag">trivial</span>' : '';
    return `
      <tr class="${r.trivial ? 'vt-trivial' : ''}">
        <td class="vt-label">${escapeHtml(r.label)}${tag}</td>
        <td class="vt-num">${fmtNum(r.prior)}</td>
        <td class="vt-num">${fmtNum(r.current)}</td>
        <td class="vt-num ${dir}">${(typeof r.abs_delta === 'number' && r.abs_delta >= 0) ? '+' : ''}${fmtNum(r.abs_delta)}</td>
        <td class="vt-num ${dir}">${pctCell}</td>
      </tr>`;
  }).join('');

  const tableHtml = table.length ? `
    <table class="variance-table">
      <thead><tr>
        <th>Line item</th><th>${escapeHtml(priorLabel)}</th><th>${escapeHtml(curLabel)}</th><th>Δ</th><th>Δ%</th>
      </tr></thead>
      <tbody>${rowsHtml}</tbody>
    </table>` : '';

  // Materiality line: what threshold was applied and how many lines it set aside.
  const threshold = data.clearly_trivial || 0;
  const nTrivial = table.filter(r => r.trivial).length;
  const materialityHtml = threshold > 0
    ? `<div class="variance-materiality">Clearly-trivial threshold: ${threshold.toLocaleString('en-GB')} · ${nTrivial} line${nTrivial !== 1 ? 's' : ''} set aside as trivial</div>`
    : '';

  // Skipped lines: located as line items but non-numeric in one or both years,
  // so no delta was computed. The audit log records them; the UI must say so
  // too — silently dropping rows would undercut the audit story.
  const skipped = data.skipped || [];
  const skippedHtml = skipped.length
    ? `<div class="variance-skipped">${skipped.length} line item${skipped.length !== 1 ? 's' : ''} skipped (non-numeric value in one or both years): ${escapeHtml(skipped.map(s => s.label).filter(Boolean).join(', '))}</div>`
    : '';

  const anomaliesHtml = anomalies.map(a => `
    <div class="review-report-item warning">
      <div class="review-report-item-label">⚠️ ${escapeHtml(a.title || 'Anomaly')}</div>
      <div class="review-report-item-msg">${escapeHtml(a.detail || '')}</div>
    </div>`).join('');

  const questionsHtml = questions.length ? `
    <div class="variance-cfo">
      <div class="variance-cfo-title">Questions for CFO</div>
      <ul>${questions.map(q => `<li>${escapeHtml(q)}</li>`).join('')}</ul>
    </div>` : '';

  const bodyHtml = (tableHtml || materialityHtml || skippedHtml || anomaliesHtml || questionsHtml)
    ? `<div class="review-report-body">${tableHtml}${materialityHtml}${skippedHtml}${anomaliesHtml}${questionsHtml}</div>`
    : '';

  const wrap = document.createElement('div');
  wrap.className = 'message ai';
  wrap.innerHTML = `
    <div class="msg-avatar ai" aria-hidden="true"><img src="../../assets/cat-head.png" alt="" /></div>
    <div class="msg-body">
      <div class="msg-role ai">ExcelCat AI · variance analysis${sheetName ? ' · ' + escapeHtml(sheetName) : ''}</div>
      <div class="review-report">
        <div class="review-report-head">
          <div class="review-report-title">Variance Analysis</div>
          <div class="review-report-badge ${badgeClass}">${badgeLabel}</div>
        </div>
        ${bodyHtml}
        ${summary ? `<div class="review-report-summary">${escapeHtml(summary)}</div>` : ''}
      </div>
    </div>`;
  messagesEl.appendChild(wrap);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}
