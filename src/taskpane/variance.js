// Variance chip flow: find the Income Statement and/or Balance Sheet tabs →
// materiality prompt (one shared threshold) → /variance → variance report card
// (tie-out checks, per-statement YoY tables, cross-statement ratios).

import { state, API_BASE, messagesEl, emptyState, sendBtn, escapeHtml } from './core';
import { addMessage, showTyping, hideTyping } from './messages';
import { readSheetByName, IS_SHEET_NAMES, BS_SHEET_NAMES, selectRangeOnSheet } from './excel';

const ROLE_NAMES = { IS: 'Income Statement', BS: 'Balance Sheet' };

// ── Variance Analysis ──────────────────────────────────
export async function triggerVariance() {
  let isSheet = null, bsSheet = null;
  try { isSheet = await readSheetByName(IS_SHEET_NAMES); } catch { isSheet = null; }
  try { bsSheet = await readSheetByName(BS_SHEET_NAMES); } catch { bsSheet = null; }
  if (!isSheet && !bsSheet) {
    addMessage('assistant', 'I couldn’t find an Income Statement or Balance Sheet tab. Rename the relevant sheets to "IS"/"Income Statement" or "BS"/"Balance Sheet" and try again.');
    return;
  }

  // Whatever exists is what gets analysed: IS only, BS only, or both — both
  // unlocks the tie-out checks and the cross-statement ratio layer.
  const statements = [];
  if (isSheet) statements.push({ role: 'IS', sheet: isSheet.name, address: isSheet.address, values: isSheet.values, formulas: isSheet.formulas });
  if (bsSheet) statements.push({ role: 'BS', sheet: bsSheet.name, address: bsSheet.address, values: bsSheet.values, formulas: bsSheet.formulas });

  // Establish materiality first (audit discipline): one shared clearly-trivial
  // threshold, pre-filled with a suggestion, then run once the user confirms.
  const suggested = suggestSharedThreshold(statements);
  const label = statements.map(s => `${s.role} · ${s.sheet}`).join(' + ');
  promptMateriality(suggested, label, statements.length > 1, trivial => runVariance(statements, trivial));
}

// Heuristic suggested threshold ≈ 1% of the largest figure in the sheet (a
// revenue proxy on the IS, a total-assets proxy on the BS — both standard
// materiality benchmarks), rounded to one significant figure. Only a
// suggestion — the user can overwrite it in the prompt.
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

// One threshold is shared across both statements, so take the SMALLER of the
// per-sheet suggestions: a BS-scaled threshold (1% of total assets) would
// swallow material IS movements, whereas a threshold that is too small merely
// filters less. Conservative beats convenient.
function suggestSharedThreshold(statements) {
  const per = statements.map(s => suggestTrivialThreshold(s.values)).filter(v => v > 0);
  return per.length ? Math.min(...per) : 0;
}

// Inline prompt card: a number input pre-filled with `suggested` + a Run button.
// We avoid window.prompt() — it's unreliable in the Office webview. onRun fires
// once, when the user clicks Run (or presses Enter).
function promptMateriality(suggested, label, isDual, onRun) {
  if (messagesEl.contains(emptyState)) messagesEl.removeChild(emptyState);

  const help = 'Line items whose absolute change is smaller than this are treated as trivial and excluded. '
    + 'In the sheets’ own units; suggested ≈ 1% of the largest figure'
    + (isDual
      ? ', taken as the smaller across the detected statements. Also used as the tolerance for the balance-sheet checks.'
      : '.');

  const wrap = document.createElement('div');
  wrap.className = 'message ai';
  wrap.innerHTML = `
    <div class="msg-avatar ai" aria-hidden="true"><img src="../../assets/cat-head.png" alt="" /></div>
    <div class="msg-body">
      <div class="msg-role ai">ExcelCat AI · materiality</div>
      <div class="materiality-prompt">
        <div class="mat-title">Set clearly-trivial threshold${label ? ' · ' + escapeHtml(label) : ''}</div>
        <div class="mat-help">${help}</div>
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

async function runVariance(statements, clearlyTrivial) {
  state.isTyping = true;
  sendBtn.disabled = true;
  showTyping();
  const stop = () => { state.isTyping = false; sendBtn.disabled = false; hideTyping(); };

  // 150s timeout — variance legitimately runs multiple LLM passes over whole
  // statements, so it gets more budget than chat; but a wedged backend must
  // still surface as an error, not an infinite spinner.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 150_000);

  try {
    const resp = await fetch(`${API_BASE}/variance`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        statements,
        clearly_trivial: clearlyTrivial,
        model: state.selectedModel,
        audit_enabled: state.auditEnabled,
      }),
      signal: controller.signal,
    });
    clearTimeout(timer);

    stop();
    if (!resp.ok) throw new Error(`Server error: ${resp.status}`);
    const data = await resp.json();
    renderVarianceReport(data, statements.map(s => s.sheet).join(' + '));
  } catch (err) {
    clearTimeout(timer);
    stop();
    const msg = err.name === 'AbortError'
      ? '⚠️ Variance analysis timed out (150s). The backend may be unresponsive.'
      : `⚠️ Variance analysis failed: ${err.message}`;
    addMessage('assistant', msg);
  }
}

// ── A1 addressing (for click-to-highlight) ─────────────
// The backend works in 0-based grid indices; converting them back to real cell
// addresses needs the used-range origin, parsed from the address we sent up.
function colLettersToIndex(letters) {
  let n = 0;
  for (const ch of letters) n = n * 26 + (ch.charCodeAt(0) - 64);
  return n - 1;
}
function indexToColLetters(i) {
  let s = '';
  i += 1;
  while (i > 0) { const rem = (i - 1) % 26; s = String.fromCharCode(65 + rem) + s; i = Math.floor((i - 1) / 26); }
  return s;
}
function parseOrigin(address) {
  const m = /^\$?([A-Za-z]+)\$?(\d+)/.exec(address || '');
  if (!m) return null;
  return { col: colLettersToIndex(m[1].toUpperCase()), row: parseInt(m[2], 10) };
}

// The range covering a row's prior and current year cells. A single continuous
// span (min col → max col) rather than a discontiguous pair, because Office.js
// can't reliably SET a multi-area selection — any column sitting between the
// two years (e.g. a spacer) is included, which reads fine visually.
function rowRange(origin, gridRow, priorCol, currentCol) {
  if (!origin || gridRow == null || priorCol == null || currentCol == null) return null;
  const sheetRow = origin.row + gridRow;
  const lo = origin.col + Math.min(priorCol, currentCol);
  const hi = origin.col + Math.max(priorCol, currentCol);
  return `${indexToColLetters(lo)}${sheetRow}:${indexToColLetters(hi)}${sheetRow}`;
}

// ── Report rendering ───────────────────────────────────
const fmtNum = n => (typeof n === 'number' && isFinite(n))
  ? n.toLocaleString('en-GB', { maximumFractionDigits: 2 }) : '—';
const fmtPct = p => (typeof p === 'number' && isFinite(p))
  ? `${p >= 0 ? '+' : ''}${(p * 100).toFixed(1)}%` : 'n/a';

const CHECK_ICONS = { pass: '✓', fail: '⚠', info: 'ℹ', skipped: '○' };

// Tie-out checks: deterministic arithmetic, shown with their residuals —
// vocabulary discipline: these are "checks" (proofs), only Pass B findings
// are "anomalies" (judgements).
function checksHtml(checks) {
  if (!checks.length) return '';
  const rows = checks.map(c => {
    const st = CHECK_ICONS[c.status] ? c.status : 'skipped';
    return `
      <div class="vt-check">
        <span class="vt-check-icon ${st}">${CHECK_ICONS[st]}</span>
        <div><span class="vt-check-label">${escapeHtml(c.label || '')}</span>
        <span class="vt-check-detail">${escapeHtml(c.detail || '')}</span></div>
      </div>`;
  }).join('');
  return `
    <div class="variance-checks">
      <div class="variance-checks-title">Checks</div>
      ${rows}
    </div>`;
}

function statementHtml(st, isDual) {
  const name = ROLE_NAMES[st.role] || st.role;
  const head = `<div class="variance-stmt-head">${escapeHtml(name)}${st.sheet ? ' · ' + escapeHtml(st.sheet) : ''}</div>`;

  // A degraded statement (empty sheet, unreadable layout) still gets its
  // section — saying what happened, never silently vanishing.
  if (st.error) {
    return `${isDual ? head : ''}<div class="variance-skipped">${escapeHtml(st.error)}</div>`;
  }

  const table = st.variance_table || [];
  const origin = parseOrigin(st.address);

  const rowsHtml = table.map(r => {
    const neg = (typeof r.abs_delta === 'number' && r.abs_delta < 0);
    const dir = neg ? 'neg' : 'pos';
    const noBase = r.flags && r.flags.includes('no_prior_base');
    const pctCell = noBase ? 'new' : fmtPct(r.pct_delta);
    const tag = r.trivial ? ' <span class="vt-tag">trivial</span>' : '';
    const range = rowRange(origin, r.row, st.prior_col, st.current_col);
    const rowClass = `${r.trivial ? 'vt-trivial' : ''}${range && st.sheet ? (r.trivial ? ' ' : '') + 'vt-click' : ''}`;
    const clickAttrs = (range && st.sheet)
      ? ` data-sheet="${escapeHtml(st.sheet)}" data-range="${range}" title="Click to highlight the source cells"`
      : '';
    return `
      <tr class="${rowClass}"${clickAttrs}>
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
        <th>Line item</th><th>${escapeHtml(st.prior_label || 'Prior')}</th><th>${escapeHtml(st.current_label || 'Current')}</th><th>Δ</th><th>Δ%</th>
      </tr></thead>
      <tbody>${rowsHtml}</tbody>
    </table>` : '';

  // Skipped lines: located as line items but non-numeric in one or both years,
  // so no delta was computed. The audit log records them; the UI must say so
  // too — silently dropping rows would undercut the audit story.
  const skipped = st.skipped || [];
  const skippedHtml = skipped.length
    ? `<div class="variance-skipped">${skipped.length} line item${skipped.length !== 1 ? 's' : ''} skipped (non-numeric value in one or both years): ${escapeHtml(skipped.map(s => s.label).filter(Boolean).join(', '))}</div>`
    : '';

  return `${isDual ? head : ''}${tableHtml}${skippedHtml}`;
}

// Cross-statement ratios (dual mode only): every figure was computed in Python
// from located cells — a ratio that couldn't be computed is stated with its
// reason, never silently dropped.
function ratiosHtml(ratios) {
  if (!ratios.length) return '';
  const fmtRatio = (v, unit) => (typeof v === 'number' && isFinite(v))
    ? (unit === 'pct' ? `${(v * 100).toFixed(1)}%` : v.toFixed(1)) : '—';
  const ok = ratios.filter(r => r.status === 'ok');
  const skipped = ratios.filter(r => r.status !== 'ok');

  const rowsHtml = ok.map(r => {
    const neg = (typeof r.delta === 'number' && r.delta < 0);
    const dir = neg ? 'neg' : 'pos';
    const delta = (typeof r.delta === 'number' && isFinite(r.delta))
      ? `${r.delta >= 0 ? '+' : ''}${r.unit === 'pct' ? (r.delta * 100).toFixed(1) + 'pp' : r.delta.toFixed(1)}`
      : '—';
    return `
      <tr>
        <td class="vt-label">${escapeHtml(r.label)}<div class="vr-basis">${escapeHtml(r.basis || '')}</div></td>
        <td class="vt-num">${fmtRatio(r.prior, r.unit)}</td>
        <td class="vt-num">${fmtRatio(r.current, r.unit)}</td>
        <td class="vt-num ${dir}">${delta}</td>
      </tr>`;
  }).join('');

  const tableHtml = ok.length ? `
    <table class="variance-table">
      <thead><tr><th>Ratio</th><th>Prior</th><th>Current</th><th>Δ</th></tr></thead>
      <tbody>${rowsHtml}</tbody>
    </table>` : '';

  const skippedHtml = skipped.length
    ? `<div class="variance-skipped">${skipped.map(r => `${escapeHtml(r.label)} skipped — ${escapeHtml(r.reason || '')}`).join(' · ')}</div>`
    : '';

  return `
    <div class="variance-stmt-head">Cross-statement ratios</div>
    ${tableHtml}${skippedHtml}`;
}

function renderVarianceReport(data, headLabel) {
  const statements = data.statements || [];
  const checks = data.checks || [];
  const ratios = data.ratios || [];
  const anomalies = data.anomalies || [];
  const questions = data.cfo_questions || [];
  const summary = data.summary || '';
  const isDual = statements.length > 1;

  if (messagesEl.contains(emptyState)) messagesEl.removeChild(emptyState);

  // Badge: failed checks + anomalies → orange; computed-but-clean → green;
  // nothing computed → neutral.
  const nFailed = checks.filter(c => c.status === 'fail').length;
  const nRows = statements.reduce((n, st) => n + (st.variance_table || []).length, 0);
  let badgeClass, badgeLabel;
  if (nFailed || anomalies.length) {
    badgeClass = 'has-issues';
    const parts = [];
    if (nFailed) parts.push(`${nFailed} check${nFailed > 1 ? 's' : ''} failed`);
    if (anomalies.length) parts.push(`${anomalies.length} anomal${anomalies.length > 1 ? 'ies' : 'y'}`);
    badgeLabel = parts.join(' · ');
  } else if (nRows) {
    badgeClass = 'clean';
    badgeLabel = '✓ No anomalies';
  } else {
    badgeClass = 'none';
    badgeLabel = 'Nothing to analyse';
  }

  const statementsHtml = statements.map(st => statementHtml(st, isDual)).join('');

  // Materiality line: what threshold was applied and how many lines it set
  // aside (across all statements).
  const threshold = data.clearly_trivial || 0;
  const nTrivial = statements.reduce(
    (n, st) => n + (st.variance_table || []).filter(r => r.trivial).length, 0);
  const materialityHtml = threshold > 0
    ? `<div class="variance-materiality">Clearly-trivial threshold: ${threshold.toLocaleString('en-GB')} · ${nTrivial} line${nTrivial !== 1 ? 's' : ''} set aside as trivial</div>`
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

  const bodyParts = `${checksHtml(checks)}${statementsHtml}${materialityHtml}${ratiosHtml(ratios)}${anomaliesHtml}${questionsHtml}`;
  const bodyHtml = bodyParts.trim()
    ? `<div class="review-report-body">${bodyParts}</div>`
    : '';

  const wrap = document.createElement('div');
  wrap.className = 'message ai';
  wrap.innerHTML = `
    <div class="msg-avatar ai" aria-hidden="true"><img src="../../assets/cat-head.png" alt="" /></div>
    <div class="msg-body">
      <div class="msg-role ai">ExcelCat AI · variance analysis${headLabel ? ' · ' + escapeHtml(headLabel) : ''}</div>
      <div class="review-report">
        <div class="review-report-head">
          <div class="review-report-title">Variance Analysis</div>
          <div class="review-report-badge ${badgeClass}">${badgeLabel}</div>
        </div>
        ${bodyHtml}
        ${summary ? `<div class="review-report-summary">${escapeHtml(summary)}</div>` : ''}
      </div>
    </div>`;

  // Click-to-highlight: a variance row knows exactly which cells its figures
  // came from (Pass A's indices + the used-range origin). One click selects
  // them in Excel — "show me where this number comes from", literally.
  wrap.querySelectorAll('tr.vt-click').forEach(tr => {
    tr.addEventListener('click', () => {
      selectRangeOnSheet(tr.dataset.sheet, tr.dataset.range).catch(() => {});
    });
  });

  messagesEl.appendChild(wrap);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}
