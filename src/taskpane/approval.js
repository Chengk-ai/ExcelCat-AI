// Approval-card flow: render the proposed tool_call, then handle the
// user's decision (approve / use AI's version / reject / reject & retry).
// Executes the frontend action tools only after an explicit Approve.
//
// See the note in chat.js about the deliberate chat ⇄ approval import cycle.

import { state, messagesEl, escapeHtml } from './core';
import { addMessage, scrollMessages } from './messages';
import { getAIResponse } from './chat';
import { writeToCellTool, createNativeChart, writeCellsToSheet, selectionContextLabel } from './excel';
import { retryDCF } from './dcf';
import { postAuditDecision } from './audit';
import { markVerifyDecision, appendVerifyLog } from './verify';

// ── Approval flow (Step 2: minimal) ────────────────────
// Tracks pending tool_calls awaiting user decision.
state.pendingApprovals = state.pendingApprovals || {};

// Present a /chat-shaped response's tool_calls: tag each with the audit
// identifiers (request_id + tool_index — how approval decisions land in the
// same trail as the proposal), then render the verify-log entry and the
// approval card. Shared by every endpoint that returns the tool_calls shape
// (chat.js, dcf.js) so the tagging logic can't drift between them.
export function presentToolCalls(data) {
  (data.tool_calls || []).forEach((tool, toolIndex) => {
    const approvalId = `appr_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
    tool.__requestId = data.request_id || null;
    tool.__toolIndex = toolIndex;
    appendVerifyLog(tool, approvalId);
    renderApprovalCard(tool, approvalId);
  });
}

export function renderApprovalCard(toolCall, approvalId) {
  // approvalId is supplied by the caller so the verify-log entry and the
  // approval card share the same identifier — that lets us update the log
  // entry with the user's decision when they click a button.
  if (!approvalId) {
    approvalId = `appr_${Date.now()}_${Math.random().toString(36).slice(2,7)}`;
  }
  state.pendingApprovals[approvalId] = toolCall;

  const status = toolCall.hook_result?.status || 'ok';
  const statusLabel = { ok: '✓ Verified', suggestion: '💡 Suggestion', warning: '⚠️ Warning' }[status] || status;

  // Describe what the tool will do, in human language.
  let description = '';
  if (toolCall.name === 'write_to_cell') {
    description = `Write <code>${escapeHtml(toolCall.args.value)}</code> to cell <code>${escapeHtml(toolCall.args.cell)}</code>`;
  } else if (toolCall.name === 'create_chart') {
    description = `Create a <code>${escapeHtml(toolCall.args.chart_type)}</code> chart from the selected range`;
  } else if (toolCall.name === 'apply_formula_pattern' || toolCall.name === 'apply_forecast') {
    // Batch write — list every cell→value pair so the audit trail is
    // visible even when the user approves with one click.
    const a = toolCall.args || {};
    const cells = a.cells || [];
    const values = a.values || [];
    const rowsHtml = cells.map((c, k) =>
      `<div class="batch-row"><code class="batch-cell">${escapeHtml(c)}</code><span class="batch-arrow">←</span><code class="batch-value">${escapeHtml(values[k] ?? '')}</code></div>`
    ).join('');
    if (toolCall.name === 'apply_forecast') {
      // Forecast batch — show the method and the audit rationale up top.
      const rangeLabel = a.range || (cells.length ? `${cells[0]}–${cells[cells.length - 1]}` : '');
      description = `
        <div class="batch-summary">
          Forecast <strong>${cells.length} cells</strong> (${escapeHtml(rangeLabel)})
        </div>
        <div class="batch-pattern">
          Method: <code>${escapeHtml(a.method || 'n/a')}</code>
        </div>
        ${a.rationale ? `<div class="batch-pattern">Rationale: ${escapeHtml(a.rationale)}</div>` : ''}
        <div class="batch-rows">${rowsHtml}</div>
      `;
    } else {
      // Pattern shown up top for the "what's the same" answer.
      description = `
        <div class="batch-summary">
          Apply pattern to <strong>${cells.length} cells</strong> (${escapeHtml(a.range || '')})
        </div>
        <div class="batch-pattern">
          Pattern: <code>${escapeHtml(a.pattern || '')}</code>
        </div>
        <div class="batch-rows">${rowsHtml}</div>
      `;
    }
  } else if (toolCall.name === 'apply_cleaning') {
    // Cleaning batch — the card IS the diff: every cell's old → new with its
    // declared fix type, plus the advisory notes that were NOT actioned.
    const a = toolCall.args || {};
    const cells = a.cells || [];
    const olds = a.old_values || [];
    const news = a.new_values || [];
    const fixes = a.fix_types || [];
    const rowsHtml = cells.map((c, k) => `
      <div class="batch-row">
        <code class="batch-cell">${escapeHtml(c)}</code>
        <code class="batch-value old">${escapeHtml(olds[k] ?? '')}</code>
        <span class="batch-arrow">→</span>
        <code class="batch-value">${escapeHtml(news[k] ?? '')}</code>
        <span class="vt-tag">${escapeHtml(fixes[k] ?? '')}</span>
      </div>`).join('');
    // Notes arrive as an array of plain sentences (contract v5). Defensive:
    // older/looser model output may still be one markdown-ish string — split
    // it on newlines and strip bullets/bold markers so the card never shows
    // a wall of raw asterisks.
    const noteItems = (Array.isArray(a.notes) ? a.notes : String(a.notes || '').split(/\r?\n+/))
      .map(s => String(s).replace(/\*\*/g, '').replace(/^\s*[-•*]\s*/, '').trim())
      .filter(Boolean);
    const notesHtml = noteItems.length ? `
      <div class="clean-notes">
        <div class="clean-notes-title">Advisory — not actioned</div>
        <ul>${noteItems.map(n => `<li>${escapeHtml(n)}</li>`).join('')}</ul>
      </div>` : '';
    description = `
      <div class="batch-summary">
        Clean <strong>${cells.length} cell${cells.length !== 1 ? 's' : ''}</strong> — mechanical fixes, old → new below
      </div>
      <div class="batch-rows">${rowsHtml}</div>
      ${notesHtml}
    `;
  } else if (toolCall.name === 'apply_dcf_template') {
    description = dcfDescriptionHtml(toolCall.args || {});
  } else {
    description = `Run <code>${escapeHtml(toolCall.name)}</code>`;
  }

  // Render warnings / suggestions block, if any.
  const warnings = toolCall.hook_result?.warnings || [];
  const suggestions = toolCall.hook_result?.suggestions || [];
  const issuesHtml = renderIssuesBlock(status, warnings, suggestions);

  // Build the action buttons.
  // - suggestion state: 4 buttons (Approve original / Use AI's version / Reject & retry / Reject)
  // - warning/ok state: 3 buttons (Approve / Reject & retry / Reject)
  // - batch (apply_formula_pattern): never offer "Use AI's version" — the
  //   reflexion suggestion is on the sample formula and we'd need symbolic
  //   re-templating to apply it across N rows. Skipped intentionally.
  const isBatch = toolCall.name === 'apply_formula_pattern' || toolCall.name === 'apply_forecast' || toolCall.name === 'apply_cleaning' || toolCall.name === 'apply_dcf_template';
  let actionsHtml;
  if (status === 'suggestion' && suggestions.length > 0 && !isBatch) {
    actionsHtml = `
      <button class="approval-btn approve"        onclick="approveToolCall('${approvalId}')">Approve original</button>
      <button class="approval-btn use-suggestion" onclick="useAISuggestion('${approvalId}')">Use AI's version</button>
      <button class="approval-btn reject-retry"   onclick="openRetryForm('${approvalId}')">Reject &amp; retry</button>
      <button class="approval-btn reject"         onclick="rejectToolCall('${approvalId}')">Reject</button>
    `;
  } else {
    const approveLabel = status === 'warning' ? 'Approve anyway' : 'Approve';
    actionsHtml = `
      <button class="approval-btn approve"      onclick="approveToolCall('${approvalId}')">${approveLabel}</button>
      <button class="approval-btn reject-retry" onclick="openRetryForm('${approvalId}')">Reject &amp; retry</button>
      <button class="approval-btn reject"       onclick="rejectToolCall('${approvalId}')">Reject</button>
    `;
  }

  // Build the card as a message in the chat stream.
  const wrap = document.createElement('div');
  wrap.className = 'message ai';
  wrap.dataset.approvalId = approvalId;
  wrap.innerHTML = `
    <div class="msg-avatar ai" aria-hidden="true">
      <img src="../../assets/cat-head.png" alt="" />
    </div>
    <div class="msg-body">
      <div class="msg-role ai">ExcelCat AI · proposed change</div>
      <div class="approval-card status-${status}">
        <div class="approval-card-head">
          <div class="approval-card-tool">${escapeHtml(toolCall.name)}</div>
          <div class="approval-card-status ${status}">${statusLabel}</div>
        </div>
        <div class="approval-card-body">${description}</div>
        ${issuesHtml}
        <div class="approval-card-actions">
          ${actionsHtml}
        </div>
        <div class="approval-retry-form">
          <label>Tell the AI what was wrong</label>
          <textarea placeholder="e.g. The formula references cells outside the data range. Try again with the actual selection."></textarea>
          <div class="approval-retry-form-actions">
            <button class="approval-btn reject-retry" onclick="submitRetryForm('${approvalId}')">Send &amp; retry</button>
            <button class="approval-btn reject"       onclick="closeRetryForm('${approvalId}')">Cancel</button>
          </div>
        </div>
        <div class="approval-card-resolution"></div>
      </div>
    </div>
  `;
  messagesEl.appendChild(wrap);
  scrollMessages();
}

// DCF approval-card body: the assumptions and their rationale ARE the
// decision surface ("AI suggests, user approves"), so they come first —
// CAPM block → WACC result, TGR, then the per-year driver rows. The raw
// cell batch follows, collapsed per sheet, for the full audit picture.
const DCF_DRIVER_LABELS = {
  revenue_growth: 'Revenue growth', ebit_margin: 'EBIT margin',
  tax_pct_ebit: 'Tax % of EBIT', dna_pct_sales: 'D&A % of sales',
  capex_pct_sales: 'CapEx % of sales', dnwc_pct_sales: 'ΔNWC % of sales',
};

function dcfDescriptionHtml(a) {
  const pct = v => (typeof v === 'number' && isFinite(v)) ? `${(v * 100).toFixed(1)}%` : '—';
  const num = v => (typeof v === 'number' && isFinite(v)) ? v.toLocaleString('en-GB') : '—';
  const rat = a.rationale || {};
  const comp = a.wacc_components || {};

  const capmHtml = `
    <div class="batch-pattern">WACC: <code>${pct(a.wacc)}</code>
      (Rf ${pct(comp.rf)}, β ${typeof comp.beta === 'number' ? comp.beta.toFixed(2) : '—'}, MRP ${pct(comp.mrp)},
      Kd ${pct(comp.cost_of_debt)}, tax ${pct(comp.tax_rate)}, D ${num(comp.debt)} / E ${num(comp.equity)})
      ${rat.wacc ? `<div class="vr-basis">${escapeHtml(rat.wacc)}</div>` : ''}</div>
    <div class="batch-pattern">Terminal growth: <code>${pct(a.terminal_growth)}</code>
      ${rat.terminal_growth ? `<div class="vr-basis">${escapeHtml(rat.terminal_growth)}</div>` : ''}</div>`;

  const drivers = a.drivers || {};
  const driversHtml = Object.keys(DCF_DRIVER_LABELS).map(k => {
    const vals = drivers[k];
    if (!Array.isArray(vals) || !vals.length) return '';
    const series = vals.map(pct).join(', ');
    const why = rat[k] ? `<div class="vr-basis">${escapeHtml(rat[k])}</div>` : '';
    return `<div class="batch-pattern">${DCF_DRIVER_LABELS[k]}: <code>${series}</code>${why}</div>`;
  }).join('');

  const sheets = a.sheets || [];
  const nCells = sheets.reduce((n, s) => n + ((s.cells || []).length), 0);
  const sheetsHtml = sheets.map(s => {
    const cells = s.cells || [];
    const values = s.values || [];
    const rowsHtml = cells.map((c, k) =>
      `<div class="batch-row"><code class="batch-cell">${escapeHtml(c)}</code><span class="batch-arrow">←</span><code class="batch-value">${escapeHtml(String(values[k] ?? ''))}</code></div>`
    ).join('');
    return `<div class="batch-pattern">Sheet <strong>${escapeHtml(s.name || '')}</strong> (${cells.length} cells)</div>
      <div class="batch-rows">${rowsHtml}</div>`;
  }).join('');

  return `
    <div class="batch-summary">
      DCF valuation — <strong>${nCells} cells</strong> across new sheets <strong>WACC</strong> + <strong>DCF</strong>
    </div>
    ${capmHtml}
    ${driversHtml}
    <div class="batch-pattern">After approval you can edit any assumption cell (WACC inputs, TGR, the driver rows) — the whole valuation recalculates.</div>
    ${sheetsHtml}
  `;
}

// Render the warnings / suggestions block for an approval card.
// Returns '' (empty string) if there's nothing to show — the card stays clean.
function renderIssuesBlock(status, warnings, suggestions) {
  if (!warnings.length && !suggestions.length) return '';

  // Warnings (hard issues)
  if (warnings.length) {
    const items = warnings.map(w => `<li>${escapeHtml(w)}</li>`).join('');
    return `
      <div class="approval-card-issues warning">
        <div class="approval-card-issues-label">⚠️ Issues found</div>
        <ul>${items}</ul>
      </div>
    `;
  }

  // Suggestions (AI's alternative versions, with diff)
  const items = suggestions.map(s => `
    <li>
      <span class="reason">${escapeHtml(s.reason || 'AI suggests an alternative.')}</span>
      <div class="diff">
        <div class="diff-row">
          <span class="diff-tag original">your version</span>
          <span class="diff-value">${escapeHtml(s.original)}</span>
        </div>
        <div class="diff-row">
          <span class="diff-tag suggested">AI suggests</span>
          <span class="diff-value">${escapeHtml(s.suggested)}</span>
        </div>
      </div>
    </li>
  `).join('');
  return `
    <div class="approval-card-issues suggestion">
      <div class="approval-card-issues-label">💡 AI has a suggestion</div>
      <ul>${items}</ul>
    </div>
  `;
}

export async function approveToolCall(approvalId) {
  const tool = state.pendingApprovals[approvalId];
  if (!tool) return;

  // Lock the buttons immediately so double-clicks don't double-execute.
  const card = document.querySelector(`[data-approval-id="${approvalId}"] .approval-card`);
  card.querySelectorAll('.approval-btn').forEach(b => b.disabled = true);

  try {
    if (tool.name === 'write_to_cell') {
      await writeToCellTool(tool.args.cell, tool.args.value);
      markApprovalResolved(approvalId, 'approved', `✓ Wrote ${tool.args.value} to ${tool.args.cell}`, 'approved');
      postAuditDecision(tool.__requestId, tool.__toolIndex, 'approve');
    } else if (tool.name === 'create_chart') {
      await createNativeChart(tool.args.chart_type, tool.args.title || 'AI Generated Chart');
      markApprovalResolved(approvalId, 'approved', `✓ Created ${tool.args.chart_type} chart`, 'approved');
      postAuditDecision(tool.__requestId, tool.__toolIndex, 'approve');
    } else if (tool.name === 'apply_dcf_template') {
      // Two-sheet template write. WACC first, so the DCF sheet's =WACC!B16
      // reference resolves the moment it lands. No rollback on partial
      // failure (same policy as the other batch tools) — the resolution
      // says exactly how far it got.
      const sheets = (tool.args.sheets || []).slice()
        .sort((a, b) => (a.name === 'WACC' ? -1 : 0) - (b.name === 'WACC' ? -1 : 0));
      let writtenSheets = 0;
      let failure = null;
      for (const s of sheets) {
        try {
          await writeCellsToSheet(s.name, s.cells || [], s.values || [], { activate: s.name === 'DCF' });
          writtenSheets += 1;
        } catch (err) {
          failure = { sheet: s.name, message: err.message };
          break;
        }
      }
      if (!failure) {
        markApprovalResolved(approvalId, 'approved',
          `✓ Created WACC + DCF sheets — edit any assumption cell to recalculate`, 'approved');
        postAuditDecision(tool.__requestId, tool.__toolIndex, 'approve',
          `wrote ${writtenSheets} template sheets (WACC, DCF)`);
      } else {
        markApprovalResolved(approvalId, 'rejected',
          `⚠️ Partial: ${writtenSheets}/${sheets.length} sheets written — failed on '${failure.sheet}': ${failure.message}`,
          'failed', `Failed on sheet ${failure.sheet}: ${failure.message}`);
        postAuditDecision(tool.__requestId, tool.__toolIndex, 'failed',
          `Partial template write: failed on sheet ${failure.sheet}: ${failure.message}`);
      }
    } else if (tool.name === 'apply_formula_pattern' || tool.name === 'apply_forecast' || tool.name === 'apply_cleaning') {
      // Batch write — loop and execute each cell→value. We do NOT roll
      // back on partial failure (Excel doesn't give us a clean tx). The
      // user gets a "Partial: K/N written" status with the failure detail.
      // apply_cleaning carries its writes in new_values (values holds
      // formulas for the other two batch tools).
      const cells = tool.args.cells || [];
      const values = tool.args.values || tool.args.new_values || [];
      let written = 0;
      let failure = null;
      for (let k = 0; k < cells.length; k++) {
        try {
          await writeToCellTool(cells[k], values[k]);
          written += 1;
        } catch (err) {
          failure = { cell: cells[k], message: err.message };
          break;
        }
      }
      const rangeLabel = tool.args.range || (cells.length ? `${cells[0]}–${cells[cells.length - 1]}` : '');
      if (!failure) {
        markApprovalResolved(approvalId, 'approved',
          `✓ Wrote ${written} cells (${rangeLabel})`, 'approved');
        postAuditDecision(tool.__requestId, tool.__toolIndex, 'approve',
          `wrote ${written} cells in batch`);
      } else {
        markApprovalResolved(approvalId, 'rejected',
          `⚠️ Partial: ${written}/${cells.length} written — failed at ${failure.cell}: ${failure.message}`,
          'failed', `Failed at ${failure.cell}: ${failure.message}`);
        postAuditDecision(tool.__requestId, tool.__toolIndex, 'failed',
          `Partial batch: ${written}/${cells.length} written; failed at ${failure.cell}: ${failure.message}`);
      }
    }
  } catch (err) {
    markApprovalResolved(approvalId, 'rejected', `⚠️ Failed: ${err.message}`, 'failed');
    postAuditDecision(tool.__requestId, tool.__toolIndex, 'failed', err.message);
  }
  delete state.pendingApprovals[approvalId];
}

// User accepts AI's suggested alternative instead of their original.
// Only available when the hook returned a suggestion for this tool_call.
export async function useAISuggestion(approvalId) {
  const tool = state.pendingApprovals[approvalId];
  if (!tool) return;

  const suggestions = tool.hook_result?.suggestions || [];
  // Currently the hook only emits suggestions on `value` for write_to_cell,
  // so we just take the first one. If we add more suggestion fields later,
  // we'd loop and apply each by `field`.
  const suggestion = suggestions.find(s => s.field === 'value');
  if (!suggestion) {
    markApprovalResolved(approvalId, 'rejected', '⚠️ No AI suggestion available', 'failed');
    return;
  }

  const card = document.querySelector(`[data-approval-id="${approvalId}"] .approval-card`);
  card.querySelectorAll('.approval-btn').forEach(b => b.disabled = true);

  try {
    if (tool.name === 'write_to_cell') {
      await writeToCellTool(tool.args.cell, suggestion.suggested);
      markApprovalResolved(approvalId, 'approved',
        `✓ Wrote AI's version (${suggestion.suggested}) to ${tool.args.cell}`, 'used-ai');
      postAuditDecision(tool.__requestId, tool.__toolIndex, 'use_ai',
        `accepted suggestion: ${suggestion.suggested}`);
    }
  } catch (err) {
    markApprovalResolved(approvalId, 'rejected', `⚠️ Failed: ${err.message}`, 'failed');
    postAuditDecision(tool.__requestId, tool.__toolIndex, 'failed', err.message);
  }
  delete state.pendingApprovals[approvalId];
}

export function rejectToolCall(approvalId) {
  const tool = state.pendingApprovals[approvalId];
  // Plain reject: no further action, no AI retry.
  markApprovalResolved(approvalId, 'rejected', 'Rejected — no changes made', 'rejected');
  if (tool) postAuditDecision(tool.__requestId, tool.__toolIndex, 'reject');
  delete state.pendingApprovals[approvalId];
}

// ── Reject & retry flow ──────────────────────────────
// Click "Reject & retry" → expand a textarea → user explains why → submit
// posts the rejection as a new user message, which the backend treats as a
// fresh turn with full conversation context.

export function openRetryForm(approvalId) {
  const card = document.querySelector(`[data-approval-id="${approvalId}"] .approval-card`);
  if (!card) return;
  const form = card.querySelector('.approval-retry-form');
  // Hide the main action buttons while the form is open, to focus attention.
  card.querySelector('.approval-card-actions').style.display = 'none';
  form.classList.add('open');
  form.querySelector('textarea').focus();
}

export function closeRetryForm(approvalId) {
  const card = document.querySelector(`[data-approval-id="${approvalId}"] .approval-card`);
  if (!card) return;
  const form = card.querySelector('.approval-retry-form');
  form.classList.remove('open');
  card.querySelector('.approval-card-actions').style.display = 'flex';
  form.querySelector('textarea').value = '';
}

export async function submitRetryForm(approvalId) {
  const tool = state.pendingApprovals[approvalId];
  if (!tool) return;

  const card = document.querySelector(`[data-approval-id="${approvalId}"] .approval-card`);
  const form = card.querySelector('.approval-retry-form');
  const reason = form.querySelector('textarea').value.trim();

  if (!reason) {
    form.querySelector('textarea').focus();
    return;
  }

  // Lock the original card and show the rejection in its resolution slot.
  // Use a forced state so reject styling wins even though we resolved via retry.
  markApprovalResolved(approvalId, 'rejected', `Rejected & asked AI to retry — "${reason}"`, 'rejected-retry', reason);
  postAuditDecision(tool.__requestId, tool.__toolIndex, 'reject_retry', reason);
  delete state.pendingApprovals[approvalId];

  // DCF proposals don't live in /chat — the retry re-runs /dcf with the same
  // statements and the user's note feeding the assumptions pass.
  if (tool.name === 'apply_dcf_template') {
    addMessage('user', `[DCF proposal rejected — retry]\nReason: ${reason}`);
    await retryDCF(reason);
    return;
  }

  // Build a structured retry message. Keeping it readable so the user can see
  // their own rejection in the chat history (audit trail), and Gemini gets
  // clear context about what was rejected and why.
  // The closing instruction is action-oriented to ensure Gemini emits a new
  // tool_call instead of treating the rejection as a conversation prompt.
  const proposalSummary = describeToolCall(tool);
  const retryMessage =
    `[Previous proposal was rejected — please retry]\n` +
    `Original tool call: ${proposalSummary}\n` +
    `Reason for rejection: ${reason}\n\n` +
    `Generate a new proposal that addresses the feedback. ` +
    `Call the appropriate tool (e.g. write_to_cell) directly with corrected arguments. ` +
    `Do not ask clarifying questions — make a concrete new proposal.`;

  // Display the retry as a normal user message, then go through the standard
  // AI response path. The backend already handles conversation context, so
  // no backend change is needed.
  const ctxLabel = selectionContextLabel();
  addMessage('user', retryMessage, ctxLabel ? { context: ctxLabel } : undefined);
  await getAIResponse(retryMessage);
}

// Compact human-readable summary of a tool_call, used in retry messages.
function describeToolCall(tool) {
  if (tool.name === 'write_to_cell') {
    return `write_to_cell(cell="${tool.args.cell}", value="${tool.args.value}")`;
  }
  if (tool.name === 'create_chart') {
    return `create_chart(chart_type="${tool.args.chart_type}")`;
  }
  return `${tool.name}(${JSON.stringify(tool.args)})`;
}

function markApprovalResolved(approvalId, kind, message, decisionKey, decisionDetail) {
  const card = document.querySelector(`[data-approval-id="${approvalId}"] .approval-card`);
  if (!card) return;
  card.classList.add('resolved');
  const resolution = card.querySelector('.approval-card-resolution');
  resolution.classList.add(kind);
  resolution.textContent = message;
  // Mirror the decision into the Verification Layer log entry, if linked.
  if (decisionKey) markVerifyDecision(approvalId, decisionKey, decisionDetail);
}
