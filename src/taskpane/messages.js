// Chat message rendering: append/rebuild the message list, typing
// indicator, markdown-lite formatting, and the per-message Copy action.

import { state, messagesEl, emptyState } from './core';

export function addMessage(role, content) {
  state.messages.push({ role, content });
  // If this is the first real message, the empty-state element is taking up
  // the messages area — clear it before appending.
  if (messagesEl.contains(emptyState)) {
    messagesEl.removeChild(emptyState);
  }
  // Append the new message directly. This preserves any non-message DOM
  // (e.g. approval cards) that other code has appended to messagesEl.
  // Typing indicator (if present) should stay BELOW new messages, so we
  // briefly remove it, append the message, then re-insert it at the end.
  const typingEl = document.getElementById('typing-indicator');
  if (typingEl) typingEl.remove();
  messagesEl.appendChild(createMessageEl(role, content));
  if (typingEl) messagesEl.appendChild(typingEl);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

export function showTyping() {
  // Only one indicator at a time.
  if (document.getElementById('typing-indicator')) return;
  const el = createTypingEl();
  el.id = 'typing-indicator';
  messagesEl.appendChild(el);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

export function hideTyping() {
  const el = document.getElementById('typing-indicator');
  if (el) el.remove();
}

export function renderMessages() {
  // Full rebuild from state. Used for: initial render, "Clear chat", and
  // recovery scenarios. NOT called on every addMessage anymore — that
  // would wipe out approval cards and other appended DOM.
  messagesEl.innerHTML = '';

  if (state.messages.length === 0) {
    messagesEl.appendChild(emptyState);
    return;
  }

  state.messages.forEach(msg => {
    messagesEl.appendChild(createMessageEl(msg.role, msg.content));
  });

  if (state.isTyping) {
    showTyping();
  }

  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function createMessageEl(role, content) {
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
      <div class="msg-content"><div class="dots"><span></span><span></span><span></span></div></div>
    </div>
  `;
  return wrap;
}

function formatContent(text) {
  // Simple markdown-lite: code blocks and inline code
  return text
    .replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}

// ── Utility actions ────────────────────────────────────
export function copyMsg(btn) {
  const content = btn.closest('.msg-body').querySelector('.msg-content').innerText;
  navigator.clipboard.writeText(content).then(() => {
    btn.textContent = '✓ Copied';
    setTimeout(() => { btn.innerHTML = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="5" y="5" width="8" height="8" rx="1.5"/><path d="M3 11V3h8"/></svg> Copy`; }, 1500);
  });
}
