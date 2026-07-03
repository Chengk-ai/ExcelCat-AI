// Entry point (webpack entry — do not rename). Feature logic lives in the
// sibling modules; this file only wires DOM events, initialises Office.js,
// and exposes the inline-onclick handlers on window. Top-level statements
// below keep the source order of the pre-split taskpane.js so execution
// order is unchanged.

import {
  state, API_BASE, chatInput, sendBtn, selPill, clearSelBtn, btnClear, btnMenu,
  auditMenu, miAuditToggle, miAuditClear, miAuditDl, panelVerify, modelSelect,
} from './core';
import { setMenu, applyAuditUi } from './audit';
import { refreshSelection } from './excel';
import { renderMessages, copyMsg } from './messages';
import { handleSend } from './chat';
import {
  approveToolCall, useAISuggestion, rejectToolCall,
  openRetryForm, closeRetryForm, submitRetryForm,
} from './approval';
import { triggerReview } from './review';
import { triggerVariance } from './variance';

state.selectedModel = 'deepseek-v4-flash';

if (modelSelect) {
  modelSelect.addEventListener('change', (e) => {
    state.selectedModel = e.target.value || 'deepseek-v4-flash';
  });
}

// ── Audit overflow menu ────────────────────────────────
if (btnMenu) {
  btnMenu.addEventListener('click', e => { e.stopPropagation(); setMenu(auditMenu.hidden); });
  document.addEventListener('click', e => {
    if (!e.target.closest('#audit-menu, #btn-menu')) setMenu(false);
  });
}

// ── Audit toggle ───────────────────────────────────────
applyAuditUi();

if (miAuditToggle) {
  miAuditToggle.addEventListener('click', () => {
    state.auditEnabled = !state.auditEnabled;
    localStorage.setItem('excelmate.auditEnabled', String(state.auditEnabled));
    applyAuditUi();
    setMenu(false);
  });
}

// Download audit trail as .md file. Backend renders on demand so the
// hot path (/chat) never pays the full-file I/O cost.
if (miAuditDl) {
  miAuditDl.addEventListener('click', async () => {
    setMenu(false);
    try {
      const resp = await fetch(`${API_BASE}/audit/view`);
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

if (miAuditClear) {
  miAuditClear.addEventListener('click', async () => {
    setMenu(false);
    // No confirmation dialog — the audit history is the user's, deleting
    // it is a normal operation. Backend wipes both audit.jsonl and audit.md.
    try {
      await fetch(`${API_BASE}/audit/clear`, { method: 'POST' });
    } catch {
      // Non-fatal: audit clear failures aren't worth interrupting the user.
    }
  });
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

// Variance Analysis chip — reads the Income Statement tab and hits /variance.
// Like Review, it bypasses chat (it sources two-year data from a named sheet,
// not the active selection).
const chipVariance = document.getElementById('chip-variance');
if (chipVariance) {
  chipVariance.addEventListener('click', () => triggerVariance());
}

// Audit-tool "learn more" (ⓘ) — toggles the inline explainer below the row.
// stopPropagation keeps the click off the row (which would run the report).
// The demo video iframe is injected lazily on first open, and only if a
// data-video id has been set — otherwise the "coming soon" placeholder stays.
document.querySelectorAll('.audit-tool-info-btn').forEach(btn => {
  btn.addEventListener('click', e => {
    e.stopPropagation();
    const info = btn.closest('.audit-tool-wrap')?.querySelector('.audit-tool-info');
    if (!info) return;
    const opening = info.hidden;
    info.hidden = !opening;
    btn.setAttribute('aria-expanded', String(opening));
    if (opening) loadToolVideo(info);
  });
});

function loadToolVideo(info) {
  const slot = info.querySelector('.audit-tool-video');
  if (!slot || slot.dataset.loaded) return;
  const id = (slot.dataset.video || '').trim();
  if (!id) return;                       // no video yet — keep the placeholder
  slot.dataset.loaded = '1';
  const iframe = document.createElement('iframe');
  iframe.src = `https://www.youtube-nocookie.com/embed/${encodeURIComponent(id)}`;
  iframe.title = 'Demo video';
  iframe.allow = 'accelerometer; clipboard-write; encrypted-media; picture-in-picture';
  iframe.allowFullscreen = true;
  slot.innerHTML = '';
  slot.appendChild(iframe);
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
  }
  // Hide the panel again — with no entries it shouldn't take up space.
  if (panelVerify) panelVerify.style.display = 'none';
  state.verifyCount = 0;
  const counter = document.getElementById('verify-count');
  if (counter) counter.textContent = '0 checks';
});

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
