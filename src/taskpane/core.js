// Shared foundation: app state, DOM references, and escapeHtml.
// Imported by every feature module — keep this file dependency-free.

// ── State ──────────────────────────────────────────────
export const state = {
  messages: [],
  selectionContext: null,
  isTyping: false,
  // In-flight request handle — lets the send button double as a Stop
  // button. stopRequested distinguishes a user cancel from the safety
  // timeout, so the error message can say the right thing.
  activeController: null,
  stopRequested: false,
  // Audit trail toggle. Persisted in localStorage. Default ON because
  // verifiability is the product's selling point — the user can opt out.
  // Sent with every /chat request and every /audit/decision call.
  auditEnabled: (localStorage.getItem('excelmate.auditEnabled') ?? 'true') === 'true',
};

// ── Backend ────────────────────────────────────────────
// Single home for the backend origin — every fetch goes through this.
export const API_BASE = 'http://127.0.0.1:8000';

// ── DOM ────────────────────────────────────────────────
export const messagesEl   = document.getElementById('messages');
export const emptyState   = document.getElementById('empty-state');
export const chatInput    = document.getElementById('chat-input');
export const sendBtn      = document.getElementById('send-btn');
export const selPill      = document.getElementById('selection-pill');
export const selLabel     = document.getElementById('selection-label');
export const clearSelBtn  = document.getElementById('clear-sel');
export const btnClear     = document.getElementById('btn-clear');
export const btnMenu      = document.getElementById('btn-menu');
export const auditMenu    = document.getElementById('audit-menu');
export const miAuditToggle= document.getElementById('mi-audit-toggle');
export const miAuditClear = document.getElementById('mi-audit-clear');
export const miAuditDl    = document.getElementById('mi-audit-download');
export const panelVerify  = document.getElementById('panel-verify');
export const modelSelect  = document.getElementById('model-select');
export const qaBar        = document.getElementById('qa-bar');
export const jumpLatest   = document.getElementById('jump-latest');

// Flip the send button between send and stop mode. state.isTyping doubles
// as the "request in flight" flag — handleSend refuses to fire while true,
// and the click handler in taskpane.js aborts the request instead.
export function setSendBusy(busy) {
  state.isTyping = busy;
  if (!sendBtn) return;
  sendBtn.classList.toggle('busy', busy);
  sendBtn.title = busy ? 'Stop request (Esc)' : 'Send (Enter)';
}

// Non-blocking toast. Used for operations that used to fail silently
// (audit download/clear) and for pre-condition nags that shouldn't enter
// the chat history.
export function showToast(text) {
  let host = document.getElementById('toast-host');
  if (!host) {
    host = document.createElement('div');
    host.id = 'toast-host';
    document.body.appendChild(host);
  }
  const t = document.createElement('div');
  t.className = 'toast';
  t.textContent = text;
  host.appendChild(t);
  setTimeout(() => t.classList.add('out'), 2600);
  setTimeout(() => t.remove(), 3000);
}

export function escapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
