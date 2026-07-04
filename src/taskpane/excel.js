// All Office.js (Excel.run) wrappers: selection tracking, cell/chart
// writes, and named-sheet reads. No rendering, no fetch — Excel I/O only.

import { state, selPill, selLabel } from './core';

// ── Selection tracking ─────────────────────────────────
export async function refreshSelection() {
  try {
    await Excel.run(async ctx => {
      const range = ctx.workbook.getSelectedRange();
      range.load(['address', 'values', 'formulas', 'rowCount', 'columnCount', 'worksheet/name']);
      await ctx.sync();

      const addr     = range.address.includes('!') ? range.address.split('!')[1] : range.address;
      const sheet    = range.worksheet.name;
      const rows     = range.rowCount;
      const cols     = range.columnCount;
      const values   = range.values;
      const formulas = range.formulas;

      state.selectionContext = { address: addr, sheet, values, formulas, rowCount: rows, columnCount: cols };
      if (selPill) {
        selLabel.textContent = `Selection: ${addr} (${rows}×${cols})`;
        selPill.classList.add('visible');
      }
    });
  } catch {
    // Demo / no Office — no-op
  }
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
