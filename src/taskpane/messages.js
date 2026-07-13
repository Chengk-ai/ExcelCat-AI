// Chat message rendering: append/rebuild the message list, typing
// indicator, markdown-lite formatting, and the per-message Copy action.
// Also owns: empty-state ⇄ quick-action-bar visibility, near-bottom-aware
// scrolling with the "new messages" pill, and chat-history persistence.

import { state, messagesEl, emptyState, qaBar, jumpLatest, escapeHtml } from './core';

// ── Persistence ────────────────────────────────────────
// Office task panes get unloaded routinely (close/reopen, host restarts);
// without this the conversation vanishes while the backend audit trail
// survives — the UI story and the audit story would diverge. Approval
// cards and report cards are deliberately NOT persisted: an unexecuted
// proposal must not resurrect as an actionable card after a reload.
const HISTORY_KEY = 'excelmate.chatHistory';

function saveMessages() {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(state.messages.slice(-200)));
  } catch { /* quota / private mode — non-fatal */ }
}

export function loadMessages() {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    if (!raw) return;
    const arr = JSON.parse(raw);
    if (Array.isArray(arr)) {
      state.messages = arr.filter(m =>
        m && typeof m.content === 'string' && (m.role === 'user' || m.role === 'assistant'));
    }
  } catch { /* corrupted history — start fresh */ }
}

export function clearSavedMessages() {
  try { localStorage.removeItem(HISTORY_KEY); } catch { /* non-fatal */ }
}

// ── Retry handler ──────────────────────────────────────
// Injected by taskpane.js (entry) so messages.js doesn't import chat.js —
// that would add a second module cycle on top of the deliberate
// chat ⇄ approval one.
let retryHandler = null;
export function setRetryHandler(fn) { retryHandler = fn; }

// ── Empty state ⇄ quick-action bar ─────────────────────
// Exactly one of the two offers the quick actions at any time: the
// empty-state launcher before the first message, the compact qa-bar after.
export function hideEmptyState() {
  if (messagesEl.contains(emptyState)) messagesEl.removeChild(emptyState);
  if (qaBar) qaBar.hidden = false;
}

// ── Scrolling ──────────────────────────────────────────
// Only auto-scroll when the user is already near the bottom; otherwise
// show the jump pill instead of yanking them away from whatever they're
// reading (e.g. a variance report further up).
const NEAR_BOTTOM_PX = 60;

function isNearBottom() {
  return messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < NEAR_BOTTOM_PX;
}

export function scrollMessages(force = false) {
  if (force || isNearBottom()) {
    messagesEl.scrollTop = messagesEl.scrollHeight;
    if (jumpLatest) jumpLatest.hidden = true;
  } else if (jumpLatest) {
    jumpLatest.hidden = false;
  }
}

// Hide the pill once the user scrolls back down on their own.
export function initScrollWatcher() {
  messagesEl.addEventListener('scroll', () => {
    if (jumpLatest && !jumpLatest.hidden && isNearBottom()) jumpLatest.hidden = true;
  });
}

// ── Messages ───────────────────────────────────────────
export function addMessage(role, content, meta) {
  state.messages.push(meta ? { role, content, meta } : { role, content });
  saveMessages();
  hideEmptyState();
  // Append the new message directly. This preserves any non-message DOM
  // (e.g. approval cards) that other code has appended to messagesEl.
  // Typing indicator (if present) should stay BELOW new messages, so we
  // briefly remove it, append the message, then re-insert it at the end.
  const typingEl = document.getElementById('typing-indicator');
  if (typingEl) typingEl.remove();
  messagesEl.appendChild(createMessageEl(role, content, meta));
  if (typingEl) messagesEl.appendChild(typingEl);
  // The user's own sends always follow to the bottom; everything else
  // respects the current reading position.
  scrollMessages(role === 'user');
}

// Staged status line under the dots: [secondsElapsed, text] pairs, timer
// driven. Wording is generic on purpose — without streaming the frontend
// can't see real backend progress, so it must not claim internal state it
// can't verify.
let typingTimer = null;

export function showTyping(stages) {
  // Only one indicator at a time.
  if (document.getElementById('typing-indicator')) return;
  const el = createTypingEl();
  el.id = 'typing-indicator';
  messagesEl.appendChild(el);
  scrollMessages();

  if (stages && stages.length) {
    const statusEl = el.querySelector('.typing-status');
    const started = Date.now();
    const tick = () => {
      const elapsed = (Date.now() - started) / 1000;
      let text = '';
      for (const [t, s] of stages) if (elapsed >= t) text = s;
      if (statusEl) statusEl.textContent = text;
    };
    tick();
    typingTimer = setInterval(tick, 1000);
  }
}

export function hideTyping() {
  if (typingTimer) { clearInterval(typingTimer); typingTimer = null; }
  const el = document.getElementById('typing-indicator');
  if (el) el.remove();
}

export function renderMessages() {
  // Full rebuild from state. Used for: initial render (incl. restored
  // history), "Clear chat", and recovery scenarios. NOT called on every
  // addMessage anymore — that would wipe out approval cards and other
  // appended DOM.
  messagesEl.innerHTML = '';

  if (state.messages.length === 0) {
    messagesEl.appendChild(emptyState);
    if (qaBar) qaBar.hidden = true;
    return;
  }
  if (qaBar) qaBar.hidden = false;

  state.messages.forEach(msg => {
    messagesEl.appendChild(createMessageEl(msg.role, msg.content, msg.meta));
  });

  if (state.isTyping) {
    showTyping();
  }

  scrollMessages(true);
}

function createMessageEl(role, content, meta) {
  const wrap = document.createElement('div');
  wrap.className = `message ${role}`;

  const avatar = document.createElement('div');
  avatar.className = `msg-avatar ${role === 'user' ? 'usr' : 'ai'}`;
  if (role === 'user') {
    avatar.textContent = 'U';
  } else {
    avatar.innerHTML = '<img src="../../assets/cat-head.png" alt="" />';
  }

  const body = document.createElement('div');
  body.className = 'msg-body';

  const roleLabel = document.createElement('div');
  roleLabel.className = `msg-role ${role === 'user' ? 'usr' : 'ai'}`;
  roleLabel.textContent = role === 'user' ? 'You' : 'ExcelCat AI';

  const contentEl = document.createElement('div');
  contentEl.className = 'msg-content';
  contentEl.innerHTML = formatContent(content);

  body.appendChild(roleLabel);
  // Selection-context tag: which data was in scope when this was sent.
  if (meta && meta.context) {
    const ctxEl = document.createElement('div');
    ctxEl.className = 'msg-context';
    ctxEl.textContent = meta.context;
    body.appendChild(ctxEl);
  }
  body.appendChild(contentEl);

  // Action buttons for AI messages
  if (role === 'assistant') {
    const actions = document.createElement('div');
    actions.className = 'msg-actions';
    actions.innerHTML = `
      <button class="msg-action-btn" onclick="copyMsg(this)">
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
          <rect x="5" y="5" width="8" height="8" rx="1.5"/>
          <path d="M3 11V3h8"/>
        </svg>
        Copy
      </button>
    `;
    // Retry: re-sends the failed request without retyping. Wired via
    // addEventListener (not inline onclick) so the original text doesn't
    // need attribute-escaping; survives renderMessages via meta.
    if (meta && meta.retry) {
      const rbtn = document.createElement('button');
      rbtn.className = 'msg-action-btn';
      rbtn.textContent = '↻ Retry';
      rbtn.addEventListener('click', () => {
        if (state.isTyping || !retryHandler) return;
        retryHandler(meta.retry);
      });
      actions.appendChild(rbtn);
    }
    body.appendChild(actions);
  }

  wrap.appendChild(avatar);
  wrap.appendChild(body);
  return wrap;
}

function createTypingEl() {
  const wrap = document.createElement('div');
  wrap.className = 'message ai typing-indicator';
  wrap.innerHTML = `
    <div class="msg-avatar ai" aria-hidden="true">
      <img src="../../assets/cat-head.png" alt="" />
    </div>
    <div class="msg-body">
      <div class="msg-role ai">ExcelCat AI</div>
      <div class="msg-content">
        <div class="dots"><span></span><span></span><span></span></div>
        <div class="typing-status"></div>
      </div>
    </div>
  `;
  return wrap;
}

function formatContent(text) {
  // Markdown-lite, escape-first: fenced code blocks are lifted out, the
  // remainder is HTML-escaped (so nothing the model or the user types can
  // inject markup), then inline code / **bold** / heading lines are
  // applied. Single-asterisk emphasis is deliberately NOT supported — it
  // would mangle Excel formulas like =A1*B2. NUL is the block-placeholder
  // sentinel: real chat text never contains it, so it can't collide.
  const blocks = [];
  let s = String(text ?? '').replace(/```(?:[\w-]*\n)?([\s\S]*?)```/g, (_, code) => {
    blocks.push(`<pre><code>${escapeHtml(code.replace(/\n$/, ''))}</code></pre>`);
    return `\u0000${blocks.length - 1}\u0000`;
  });
  s = escapeHtml(s)
    .replace(/`([^`\n]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
    .replace(/^#{1,6}\s+(.+)$/gm, '<strong>$1</strong>');
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => blocks[Number(i)]);
}

// ── Utility actions ────────────────────────────────────
export function copyMsg(btn) {
  const content = btn.closest('.msg-body').querySelector('.msg-content').innerText;
  navigator.clipboard.writeText(content).then(() => {
    btn.textContent = '✓ Copied';
    setTimeout(() => { btn.innerHTML = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="5" y="5" width="8" height="8" rx="1.5"/><path d="M3 11V3h8"/></svg> Copy`; }, 1500);
  });
}
