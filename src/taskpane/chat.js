// Chat send path: user text → /chat → text reply or tool_calls
// (verify-log entry + approval card per tool_call).
//
// Note: chat.js and approval.js import each other (getAIResponse renders
// approval cards; submitRetryForm re-enters getAIResponse). The cycle is
// safe — each side only calls the other at event time, never during module
// evaluation — but don't add top-level calls across this boundary.

import { state, API_BASE, chatInput, sendBtn } from './core';
import { addMessage, showTyping, hideTyping } from './messages';
import { appendVerifyLog } from './verify';
import { renderApprovalCard } from './approval';

// ── Core send logic ────────────────────────────────────
export async function handleSend() {
  const text = chatInput.value.trim();
  if (!text || state.isTyping) return;

  addMessage('user', text);
  chatInput.value = '';
  chatInput.style.height = 'auto';

  await getAIResponse(text);

}

// ── AI response ──────────────
export async function getAIResponse(userText) {
  state.isTyping = true;
  sendBtn.disabled = true;
  showTyping();

  // 90s timeout — reflexion can run up to 5 LLM calls sequentially.
  // Without this, a hung backend leaves the typing indicator spinning
  // forever with no way for the user to recover.
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 90_000);

  try {
    const response = await fetch(`${API_BASE}/chat`, {
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
