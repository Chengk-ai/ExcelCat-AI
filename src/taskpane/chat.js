// Chat send path: user text → /chat → text reply or tool_calls
// (verify-log entry + approval card per tool_call).
//
// Note: chat.js and approval.js import each other (getAIResponse renders
// approval cards; submitRetryForm re-enters getAIResponse). The cycle is
// safe — each side only calls the other at event time, never during module
// evaluation — but don't add top-level calls across this boundary.

import { state, API_BASE, chatInput, setSendBusy } from './core';
import { addMessage, showTyping, hideTyping } from './messages';
import { presentToolCalls } from './approval';
import { selectionContextLabel } from './excel';

// Timer-driven status wording under the typing dots. Generic on purpose —
// without streaming the frontend can't see real backend progress, so it
// must not claim internal state it can't verify.
const CHAT_STAGES = [
  [0,  'Thinking…'],
  [7,  'Drafting a response…'],
  [20, 'Running verification checks…'],
  [45, 'Still working — long requests can take up to 90 seconds…'],
];

// ── Core send logic ────────────────────────────────────
export async function handleSend() {
  const text = chatInput.value.trim();
  if (!text || state.isTyping) return;

  // Stamp the message with the selection context that rides along with it,
  // so the transcript shows which data shaped each turn.
  const ctxLabel = selectionContextLabel();
  addMessage('user', text, ctxLabel ? { context: ctxLabel } : undefined);
  chatInput.value = '';
  chatInput.style.height = 'auto';

  await getAIResponse(text);

}

// ── AI response ──────────────
export async function getAIResponse(userText) {
  setSendBusy(true);
  showTyping(CHAT_STAGES);

  // 90s timeout — reflexion can run up to 5 LLM calls sequentially.
  // Without this, a hung backend leaves the typing indicator spinning
  // forever with no way for the user to recover. The same controller
  // powers the Stop button (send button in busy mode); stopRequested
  // tells a user cancel apart from this safety timeout.
  const controller = new AbortController();
  state.activeController = controller;
  state.stopRequested = false;
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

    if (!response.ok) throw new Error(`Server error: ${response.status}`);

    const data = await response.json();
    hideTyping();

    if (data.tool_calls && data.tool_calls.length > 0) {
      // If AI also has any text reply, show it first as context.
      if (data.reply && data.reply.trim()) {
        addMessage('assistant', data.reply);
      }

      // Render each tool_call as an approval card. Nothing executes
      // until the user clicks Approve.
      presentToolCalls(data);
      return;
    }
    addMessage('assistant', data.reply);

  } catch (err) {
    hideTyping();
    if (err.name === 'AbortError' && state.stopRequested) {
      addMessage('assistant', '⏹️ Stopped — the request was cancelled and nothing was changed.');
    } else if (err.name === 'AbortError') {
      addMessage('assistant',
        '⚠️ No response after 90 seconds, so I stopped waiting. The backend may be busy or stuck — press Retry to try again.',
        { retry: userText });
    } else {
      addMessage('assistant',
        `⚠️ I couldn't reach the ExcelCat backend — check it's running at 127.0.0.1:8000, then press Retry.\n\n\`${err.message}\``,
        { retry: userText });
    }
  } finally {
    clearTimeout(timeout);
    state.activeController = null;
    setSendBusy(false);
    hideTyping();
  }
}
