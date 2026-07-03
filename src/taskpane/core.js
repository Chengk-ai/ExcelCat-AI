// Shared foundation: app state, DOM references, and escapeHtml.
// Imported by every feature module — keep this file dependency-free.

// ── State ──────────────────────────────────────────────
export const state = {
  messages: [],
  selectionContext: null,
  isTyping: false,
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

export function escapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
