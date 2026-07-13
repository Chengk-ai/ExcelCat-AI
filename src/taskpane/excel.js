// All Office.js (Excel.run) wrappers: selection tracking, cell/chart
// writes, and named-sheet reads. No rendering, no fetch — Excel I/O only.

import { state, selPill, selLabel } from './core';

// ── Selection tracking ─────────────────────────────────
// Cap on cells whose values/formulas ride along as chat context. Above this,
// loading the payload can freeze the webview, and the whole context is
// re-sent in every subsequent /chat body. Whole-column/row clicks are first
// clipped to the used range, so the cap only bites on genuinely huge data
// selections — those keep address/dimensions but drop values (values: []
// keeps the payload compatible with the backend's SelectionContext schema).
const MAX_CONTEXT_CELLS = 20000;

// Reads are async and can finish out of order — an older (bigger, slower)
// selection must not overwrite a newer one. Only the latest call commits.
let selectionSeq = 0;

export async function refreshSelection() {
  const seq = ++selectionSeq;
  try {
    await Excel.run(async ctx => {
      let range = ctx.workbook.getSelectedRange();
      range.load(['address', 'rowCount', 'columnCount', 'worksheet/name']);
      await ctx.sync();

      const sheet = range.worksheet.name;

      // A column-header click "selects" a million rows; the data the user
      // means is the intersection with the used range.
      if (range.rowCount * range.columnCount > MAX_CONTEXT_CELLS) {
        const used = range.worksheet.getUsedRangeOrNullObject(true);
        const clipped = range.getIntersectionOrNullObject(used);
        clipped.load(['address', 'rowCount', 'columnCount', 'isNullObject']);
        await ctx.sync();
        if (!clipped.isNullObject) range = clipped;
      }

      const addr = range.address.includes('!') ? range.address.split('!')[1] : range.address;
      const rows = range.rowCount;
      const cols = range.columnCount;

      const tooLarge = rows * cols > MAX_CONTEXT_CELLS;
      let values = [];
      let formulas = [];
      if (!tooLarge) {
        range.load(['values', 'formulas']);
        await ctx.sync();
        values = range.values;
        formulas = range.formulas;
      }

      if (seq !== selectionSeq) return; // superseded by a newer selection

      state.selectionContext = { address: addr, sheet, values, formulas, rowCount: rows, columnCount: cols, tooLarge };
      if (selPill) {
        selLabel.textContent = `Selection: ${addr} (${rows}×${cols})${tooLarge ? ' · too large, values not attached' : ''}`;
        selPill.classList.add('visible');
      }
    });
  } catch {
    // Demo / no Office — no-op
  }
}

// Compact label of the selection currently held as context, e.g.
// "Sheet1!A1:B10 · 12×2". Stamped onto each sent message so the transcript
// records which data shaped that turn — the chat half of the audit story.
// Null when no selection context is held.
export function selectionContextLabel() {
  const c = state.selectionContext;
  if (!c) return null;
  return `${c.sheet}!${c.address} · ${c.rowCount}×${c.columnCount}`
    + (c.tooLarge ? ' · values not attached (too large)' : '');
}

// Write the Chart Function
export async function createNativeChart(chartType, title = 'AI Generated Chart') {
  // Throws on failure — caller (approve handler) shows the resolution.
  await Excel.run(async (ctx) => {
    const sheet = ctx.workbook.worksheets.getActiveWorksheet();
    const range = ctx.workbook.getSelectedRange();
    const chart = sheet.charts.add(chartType, range, Excel.ChartSeriesBy.columns);
    chart.title.text = title;
    chart.format.fill.setSolidColor("#ffffff");
    await ctx.sync();
  });
}

// Write to Cell Function
export async function writeToCellTool(cellAddress, value) {
  // Throws on failure — caller (approve handler) shows the resolution.
  await Excel.run(async (ctx) => {
    const sheet = ctx.workbook.worksheets.getActiveWorksheet();
    const cell = sheet.getRange(cellAddress);
    cell.values = [[value]];
    await ctx.sync();
  });
}

// Read a worksheet's used range by trying a list of candidate names (the
// statement tab can be called "IS", "Income Statement", "P&L", …). Returns
// { name, address, values, formulas } for the first match, or null if no
// candidate sheet exists / they're all empty. Unlike refreshSelection() this
// reads a NAMED sheet, not the active selection — variance needs the whole
// statement regardless of what the user has clicked.
export async function readSheetByName(candidates) {
  let found = null;
  try {
    await Excel.run(async ctx => {
      const sheets = ctx.workbook.worksheets;
      const probes = candidates.map(name => {
        const ws = sheets.getItemOrNullObject(name);
        const used = ws.getUsedRangeOrNullObject(true);
        ws.load(['name', 'isNullObject']);
        used.load(['address', 'values', 'formulas', 'isNullObject']);
        return { ws, used };
      });
      await ctx.sync();
      for (const p of probes) {
        if (!p.ws.isNullObject && !p.used.isNullObject) {
          const addr = p.used.address.includes('!')
            ? p.used.address.split('!')[1]
            : p.used.address;
          found = { name: p.ws.name, address: addr, values: p.used.values, formulas: p.used.formulas };
          break;
        }
      }
    });
  } catch {
    // Demo / no Office — leave found null; the caller surfaces a message.
  }
  return found;
}

export const IS_SHEET_NAMES = [
  'IS', 'Income Statement', 'Income statement', 'income statement',
  'P&L', 'P & L', 'Profit and Loss', 'Profit & Loss', 'PnL', 'P and L',
];

export const BS_SHEET_NAMES = [
  'BS', 'Balance Sheet', 'Balance sheet', 'balance sheet',
  'SOFP', 'Statement of Financial Position', 'Statement of financial position',
];

export const CF_SHEET_NAMES = [
  'CF', 'Cash Flow', 'Cash flow', 'cash flow',
  'Cash Flow Statement', 'Statement of Cash Flows', 'Cashflow', 'SCF',
];

// True if a worksheet with this exact name exists (even when empty —
// readSheetByName can't answer that, because it returns null for an existing
// sheet whose used range is empty). Used for collision checks before a
// template write creates new sheets.
export async function sheetExists(name) {
  return (await anySheetExists([name])) !== null;
}

// First existing sheet name from `names`, or null — ONE Excel.run for the
// whole list (probing N sheets one call at a time pays N round-trips).
export async function anySheetExists(names) {
  let found = null;
  try {
    await Excel.run(async ctx => {
      const probes = names.map(n => {
        const ws = ctx.workbook.worksheets.getItemOrNullObject(n);
        ws.load(['name', 'isNullObject']);
        return ws;
      });
      await ctx.sync();
      const hit = probes.find(p => !p.isNullObject);
      if (hit) found = hit.name;
    });
  } catch {
    // Demo / no Office — treat as absent.
  }
  return found;
}

// Write a batch of cells onto a NAMED sheet, creating the sheet if it does not
// exist. One Excel.run for the whole batch. "=" strings go through
// range.formulas (so Excel parses them as formulas); everything else through
// range.values, with numeric strings coerced so assumptions land as numbers,
// not text. Unlike writeToCellTool this never touches the ACTIVE sheet — a
// template write must land on its own tab regardless of what the user has
// open. Throws on failure — caller shows the resolution.
export async function writeCellsToSheet(sheetName, cells, values, { activate = false } = {}) {
  await Excel.run(async ctx => {
    const sheets = ctx.workbook.worksheets;
    let sheet = sheets.getItemOrNullObject(sheetName);
    sheet.load(['isNullObject']);
    await ctx.sync();
    if (sheet.isNullObject) {
      sheet = sheets.add(sheetName);
    }
    for (let k = 0; k < cells.length; k++) {
      const range = sheet.getRange(cells[k]);
      const v = values[k];
      if (typeof v === 'string' && v.startsWith('=')) {
        range.formulas = [[v]];
      } else if (typeof v === 'string' && v.trim() !== '' && isFinite(Number(v))) {
        range.values = [[Number(v)]];
      } else {
        range.values = [[v]];
      }
    }
    if (activate) sheet.activate();
    await ctx.sync();
  });
}

// Activate a sheet and select a range (e.g. "C5:E5"). Used by the variance
// report's click-to-highlight: clicking a table row selects the source cells
// so a figure can be verified where it actually lives. Throws on failure —
// callers swallow it (a click that does nothing beats a crash).
export async function selectRangeOnSheet(sheetName, rangeAddress) {
  await Excel.run(async ctx => {
    const sheet = ctx.workbook.worksheets.getItem(sheetName);
    sheet.activate();
    sheet.getRange(rangeAddress).select();
    await ctx.sync();
  });
}
