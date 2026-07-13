// Entry point (webpack entry — do not rename). Feature logic lives in the
// sibling modules; this file only wires DOM events, initialises Office.js,
// and exposes the inline-onclick handlers on window. Top-level statements
// below keep the source order of the pre-split taskpane.js so execution
// order is unchanged.

import {
  state, API_BASE, chatInput, sendBtn, selPill, clearSelBtn, btnClear, btnMenu,
  auditMenu, miAuditToggle, miAuditClear, miAuditDl, panelVerify, modelSelect,
  jumpLatest, showToast,
} from './core';
import { setMenu, applyAuditUi } from './audit';
import { refreshSelection } from './excel';
import {
  renderMessages, copyMsg, loadMessages, clearSavedMessages,
  setRetryHandler, initScrollWatcher, scrollMessages,
} from './messages';
import { handleSend, getAIResponse } from './chat';
import {
  approveToolCall, useAISuggestion, rejectToolCall,
  openRetryForm, closeRetryForm, submitRetryForm,
} from './approval';
import { triggerReview } from './review';
import { triggerVariance } from './variance';
import { triggerDCF } from './dcf';

state.selectedModel = 'gemini-2.5-flash';

// ── Restore persisted chat + shared wiring ─────────────
// History survives task-pane reloads (Office unloads panes routinely);
// renderMessages also syncs the empty-state ⇄ qa-bar visibility.
loadMessages();
renderMessages();
// Retry handler injected here to avoid a messages → chat import cycle.
setRetryHandler(text => getAIResponse(text));
initScrollWatcher();
if (jumpLatest) {
  jumpLatest.addEventListener('click', () => scrollMessages(true));
}

if (modelSelect) {
  modelSelect.addEventListener('change', (e) => {
    state.selectedModel = e.target.value || 'gemini-2.5-flash';
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
// hot path (/chat) never pays the full-file I/O cost. Failures surface
// as a toast — "clicked and nothing happened" is the worst outcome.
if (miAuditDl) {
  miAuditDl.addEventListener('click', async () => {
    setMenu(false);
    try {
      const resp = await fetch(`${API_BASE}/audit/view`);
      if (!resp.ok) {
        showToast("Couldn't download the audit trail — backend error");
        return;
      }
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'audit.md';
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      showToast("Couldn't download the audit trail — backend unreachable");
    }
  });
}

if (miAuditClear) {
  miAuditClear.addEventListener('click', async () => {
    setMenu(false);
    // No confirmation dialog — the audit history is the user's, deleting
    // it is a normal operation. Backend wipes both audit.jsonl and audit.md.
    // The outcome is toasted either way so the click is never silent.
    try {
      const resp = await fetch(`${API_BASE}/audit/clear`, { method: 'POST' });
      showToast(resp.ok ? 'Audit trail cleared' : "Couldn't clear the audit trail — backend error");
    } catch {
      showToast("Couldn't clear the audit trail — backend unreachable");
    }
  });
}

// ── Office Init ───────────────────────────────────────
Office.onReady(info => {
  if (info.host === Office.HostType.Excel) {
    console.log('Office.js ready – Excel');
    // Auto-refresh selection whenever user changes it. Trailing debounce:
    // drag-selecting fires a burst of events, and each refresh is a full
    // Excel round-trip — only the settled selection is worth paying for.
    let selTimer = null;
    Excel.run(async ctx => {
      ctx.workbook.onSelectionChanged.add(() => {
        clearTimeout(selTimer);
        selTimer = setTimeout(refreshSelection, 200);
      });
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

// ── Send on Enter / stop on Escape ─────────────────────
chatInput.addEventListener('keydown', e => {
  // isComposing / keyCode 229: Enter inside an IME composition (e.g.
  // Chinese pinyin) confirms the candidate — it must not send the message.
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
    e.preventDefault();
    handleSend();
  }
  if (e.key === 'Escape' && state.activeController) {
    state.stopRequested = true;
    state.activeController.abort();
  }
});

// Send OR stop: while a request is in flight the same button aborts it.
sendBtn.addEventListener('click', () => {
  if (state.activeController) {
    state.stopRequested = true;
    state.activeController.abort();
    return;
  }
  handleSend();
});

// ── Quick action chips (empty-state launcher + persistent qa-bar) ──────
document.querySelectorAll('.chip[data-prompt], .qa-bar-chip[data-prompt]').forEach(chip => {
  chip.addEventListener('click', () => {
    // No selection → the round-trip would only come back with "please select
    // some data first". Nag via toast — NOT a chat message, which would kill
    // the empty-state launcher and pollute the history — and still stage the
    // prompt so the user can select a range and just press Enter.
    // Non-blocking: sending anyway remains the user's choice.
    if (!state.selectionContext) {
      showToast('Select a data range in Excel first — the prompt is staged below.');
    }
    chatInput.value = chip.dataset.prompt;
    chatInput.dispatchEvent(new Event('input'));
    chatInput.focus();
  });
});

// Review Assumptions — triggers the review endpoint directly, not chat.
// Two entry points: the empty-state row and the persistent qa-bar chip.
for (const id of ['chip-review', 'qa-bar-review']) {
  const el = document.getElementById(id);
  if (el) el.addEventListener('click', () => triggerReview());
}

// Variance Analysis — reads the statement tabs and hits /variance. Like
// Review, it bypasses chat (it sources two-year data from named sheets,
// not the active selection). Same two entry points.
for (const id of ['chip-variance', 'qa-bar-variance']) {
  const el = document.getElementById(id);
  if (el) el.addEventListener('click', () => triggerVariance());
}

// DCF Valuation — reads IS/CF (+BS) tabs and hits /dcf. The chip carries no
// hidden prompt; the behaviour lives in backend/skills/dcf.md.
for (const id of ['chip-dcf', 'qa-bar-dcf']) {
  const el = document.getElementById(id);
  if (el) el.addEventListener('click', () => triggerDCF());
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
  clearSavedMessages();
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
