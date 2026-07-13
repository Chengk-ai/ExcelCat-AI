// DCF chip flow: data pre-flight (find IS/CF (+BS), count historical years,
// state the requirement) → setup card (forecast years, optional market data)
// → /dcf → the standard approval-card path (the backend returns the /chat
// tool_calls shape, so verify-log + approval card + audit decisions are all
// reused unchanged).
//
// dcf.js and approval.js deliberately import each other (reject-and-retry
// re-enters runDcf with the user's note) — same event-time-only pattern as
// the chat ⇄ approval cycle; don't add top-level calls across the boundary.

import { state, API_BASE, messagesEl, escapeHtml, setSendBusy, showToast } from './core';
import { addMessage, showTyping, hideTyping, hideEmptyState, scrollMessages } from './messages';
import { readSheetByName, anySheetExists, IS_SHEET_NAMES, BS_SHEET_NAMES, CF_SHEET_NAMES } from './excel';
import { presentToolCalls } from './approval';

const MIN_HISTORY_YEARS = 3;

const DCF_STAGES = [
  [0,  'Reading the statements…'],
  [10, 'Locating the FCF components…'],
  [35, 'Deriving drivers and proposing assumptions…'],
  [80, 'Still working — a full DCF build can take a couple of minutes…'],
];

// The last run's inputs, kept so "Reject & retry" can re-run the SAME data
// with the user's note — without re-reading the workbook mid-conversation.
let lastRun = null;

export async function triggerDCF() {
  if (state.isTyping) {
    showToast('Hold on — another request is still running.');
    return;
  }

  // Template collision first — cheaper than reading three tabs. One probe
  // call covers both target names.
  const collision = await anySheetExists(['DCF', 'WACC']);
  if (collision) {
    addMessage('assistant',
      `A "${collision}" sheet already exists in this workbook. Rename or delete it first — I won't overwrite an existing model.`);
    return;
  }

  // The three statement reads are independent — run them concurrently
  // (Office.js queues the underlying requests; this saves two round-trips).
  const [isSheet, bsSheet, cfSheet] = await Promise.all([
    readSheetByName(IS_SHEET_NAMES).catch(() => null),
    readSheetByName(BS_SHEET_NAMES).catch(() => null),
    readSheetByName(CF_SHEET_NAMES).catch(() => null),
  ]);

  if (!isSheet || !cfSheet) {
    const missing = [!isSheet && 'Income Statement ("IS")', !cfSheet && 'Cash Flow ("CF")'].filter(Boolean);
    addMessage('assistant',
      `A DCF needs at least the past ${MIN_HISTORY_YEARS} years of Income Statement AND Cash Flow data. `
      + `Missing tab${missing.length > 1 ? 's' : ''}: ${missing.join(', ')}. `
      + 'The Balance Sheet is optional (it auto-locates cash and debt for the equity bridge).');
    return;
  }

  const statements = [
    { role: 'IS', sheet: isSheet.name, address: isSheet.address, values: isSheet.values, formulas: isSheet.formulas },
  ];
  if (bsSheet) statements.push({ role: 'BS', sheet: bsSheet.name, address: bsSheet.address, values: bsSheet.values, formulas: bsSheet.formulas });
  statements.push({ role: 'CF', sheet: cfSheet.name, address: cfSheet.address, values: cfSheet.values, formulas: cfSheet.formulas });

  promptDcfSetup(statements, { IS: isSheet, BS: bsSheet, CF: cfSheet });
}

// ── Deterministic year pre-scan (display only — Pass A stays authoritative) ──
// Looks for 4-digit years (and 'YY shorthand) in the first rows of the used
// range, excluding estimate labels (…E, Est, F suffixes). Regex on labels, no
// LLM — it exists so the setup card can SAY how much history was detected and
// hold the Continue button below the minimum.
function scanHistoricalYears(values) {
  const years = new Set();
  const rows = (values || []).slice(0, 12);
  for (const row of rows) {
    for (const v of row || []) {
      if (v == null) continue;
      const s = String(v).trim();
      if (/(\d\s*)?(E|EST|F)\s*$/i.test(s) && /\d/.test(s)) continue; // estimate column
      let m = /(19|20)(\d{2})(?!\d)/.exec(s);
      if (m) { years.add(Number(`${m[1]}${m[2]}`)); continue; }
      m = /'(\d{2})(?!\d)/.exec(s);
      if (m) years.add(2000 + Number(m[1]));
    }
  }
  return [...years].sort((a, b) => a - b);
}

function yearsSummary(label, sheet) {
  if (!sheet) {
    return `<div class="vt-check"><span class="vt-check-icon skipped">○</span><div><span class="vt-check-label">${escapeHtml(label)}</span> <span class="vt-check-detail">not found — optional; cash/debt can be entered below</span></div></div>`;
  }
  const years = scanHistoricalYears(sheet.values);
  const ok = years.length >= MIN_HISTORY_YEARS;
  const detail = years.length
    ? `${years[0]}–${years[years.length - 1]}, ${years.length} historical year${years.length !== 1 ? 's' : ''} detected`
    : 'no year labels detected';
  return `<div class="vt-check"><span class="vt-check-icon ${ok ? 'pass' : 'fail'}">${ok ? '✓' : '⚠'}</span><div><span class="vt-check-label">${escapeHtml(label)} · ${escapeHtml(sheet.name)}</span> <span class="vt-check-detail">${escapeHtml(detail)}</span></div></div>`;
}

// ── Setup / data pre-flight card ───────────────────────
function promptDcfSetup(statements, sheets) {
  hideEmptyState();

  const isYears = scanHistoricalYears(sheets.IS.values);
  const cfYears = scanHistoricalYears(sheets.CF.values);
  const meetsMinimum = isYears.length >= MIN_HISTORY_YEARS && cfYears.length >= MIN_HISTORY_YEARS;

  const wrap = document.createElement('div');
  wrap.className = 'message ai';
  wrap.innerHTML = `
    <div class="msg-avatar ai" aria-hidden="true"><img src="../../assets/cat-head.png" alt="" /></div>
    <div class="msg-body">
      <div class="msg-role ai">ExcelCat AI · DCF setup</div>
      <div class="materiality-prompt">
        <div class="mat-title">DCF data check</div>
        <div class="mat-help">A DCF needs at least the past ${MIN_HISTORY_YEARS} years of Income Statement and Cash Flow data (estimate columns like '26E are excluded). The Balance Sheet is optional — it auto-locates cash and debt.</div>
        ${yearsSummary('Income Statement', sheets.IS)}
        ${yearsSummary('Cash Flow', sheets.CF)}
        ${yearsSummary('Balance Sheet', sheets.BS)}
        <div class="mat-row"><label class="mat-help" style="min-width:150px">Forecast years (3–10)</label>
          <input type="number" class="mat-input dcf-years" value="5" min="3" max="10" step="1" /></div>
        <div class="mat-row"><label class="mat-help" style="min-width:150px">Shares outstanding (opt.)</label>
          <input type="number" class="mat-input dcf-shares" placeholder="e.g. 10456" min="0" step="any" /></div>
        <div class="mat-row"><label class="mat-help" style="min-width:150px">Current share price (opt.)</label>
          <input type="number" class="mat-input dcf-price" placeholder="e.g. 122.42" min="0" step="any" /></div>
        <div class="mat-row"><label class="mat-help" style="min-width:150px">Cash override (opt.)</label>
          <input type="number" class="mat-input dcf-cash" placeholder="beats the BS figure" step="any" /></div>
        <div class="mat-row"><label class="mat-help" style="min-width:150px">Debt override (opt.)</label>
          <input type="number" class="mat-input dcf-debt" placeholder="beats the BS figure" step="any" /></div>
        <div class="mat-row">
          <button class="mat-run dcf-go" type="button" ${meetsMinimum ? '' : 'disabled'}>${meetsMinimum ? 'Build DCF' : `Need ≥${MIN_HISTORY_YEARS} years of IS and CF history`}</button>
        </div>
      </div>
    </div>`;

  const btn = wrap.querySelector('.dcf-go');
  const num = sel => {
    const v = parseFloat(wrap.querySelector(sel).value);
    return isFinite(v) ? v : null;
  };
  btn.addEventListener('click', () => {
    const years = num('.dcf-years');
    const forecastYears = (years && years >= 3 && years <= 10) ? Math.round(years) : 5;
    btn.disabled = true;
    btn.textContent = 'Building…';
    wrap.querySelectorAll('.mat-input').forEach(i => i.disabled = true);
    lastRun = {
      statements,
      forecast_years: forecastYears,
      shares: num('.dcf-shares'),
      current_price: num('.dcf-price'),
      cash_override: num('.dcf-cash'),
      debt_override: num('.dcf-debt'),
    };
    runDcf();
  });

  messagesEl.appendChild(wrap);
  scrollMessages(true);
}

// Re-entry point for the approval card's "Reject & retry": same statements
// and setup, plus the user's note for the assumptions pass.
export async function retryDCF(reason) {
  if (!lastRun) return;
  await runDcf(reason);
}

async function runDcf(userNote) {
  if (!lastRun) return;
  setSendBusy(true);
  showTyping(DCF_STAGES);

  // 210s: the backend's worst case is Pass A (60s, per-statement in parallel)
  // + the contracted DCF pass (120s) + hook/reflexion overhead — a 180s client
  // budget would race the backend's own give-up point.
  const controller = new AbortController();
  state.activeController = controller;
  state.stopRequested = false;
  const timer = setTimeout(() => controller.abort(), 210_000);

  try {
    const resp = await fetch(`${API_BASE}/dcf`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ...lastRun,
        user_note: userNote || null,
        model: state.selectedModel,
        audit_enabled: state.auditEnabled,
      }),
      signal: controller.signal,
    });
    if (!resp.ok) throw new Error(`Server error: ${resp.status}`);
    const data = await resp.json();
    hideTyping();

    if (data.tool_calls && data.tool_calls.length > 0) {
      if (data.reply && data.reply.trim()) addMessage('assistant', data.reply);
      presentToolCalls(data);
      return;
    }
    addMessage('assistant', data.reply || 'The DCF run returned nothing — please try again.');
  } catch (err) {
    hideTyping();
    let msg;
    if (err.name === 'AbortError' && state.stopRequested) {
      msg = '⏹️ Stopped — the DCF build was cancelled.';
    } else if (err.name === 'AbortError') {
      msg = '⚠️ The DCF build timed out after 210 seconds — the backend looks unresponsive.';
    } else {
      msg = `⚠️ DCF build failed: ${err.message}`;
    }
    addMessage('assistant', msg);
  } finally {
    clearTimeout(timer);
    state.activeController = null;
    setSendBusy(false);
    hideTyping();
  }
}
