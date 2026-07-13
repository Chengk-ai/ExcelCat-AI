# Skill: DCF Valuation
version: 1
last_updated: 2026-07-07

## Purpose
Build a discounted cash flow valuation from the workbook's historical financial
statements, written into TWO new worksheets — `WACC` (CAPM build-up) and `DCF`
(driver-based forecast, discounting, terminal value, and the bridge from
enterprise value to an implied share price). The template below is fixed: every
run produces the same layout, so a reviewer always knows where each assumption
lives and every downstream figure is a formula that recalculates when an
assumption cell is edited.

## Inputs you are given
- The derived historical series and driver ratios (revenue growth, EBIT margin,
  tax as % of EBIT, D&A / CapEx / ΔNWC as % of sales), computed deterministically
  from the located statement rows. **Treat them as authoritative — never
  recompute or restate a historical figure.**
- Coverage warnings naming anything that could not be located. Echo them in your
  reply; never fabricate a missing component.
- The requested number of forecast years, and optionally the user's shares
  outstanding, current share price, and cash/debt overrides.

## Your job
1. **Suggest the assumptions** — every one with a one-sentence rationale tied to
   the historical evidence (and any business context supplied):
   - CAPM components: risk-free rate, beta, market risk premium, cost of debt,
     tax rate, and the debt / equity amounts for the weights.
   - Terminal growth rate (TGR).
   - Per-forecast-year driver values: revenue growth %, EBIT margin %, tax % of
     EBIT, D&A % of sales, CapEx % of sales, ΔNWC % of sales. Base them on the
     historical averages/trend (e.g. recent-3-year average, or a fade from the
     latest value toward the average); say which basis you used.
2. **Emit the complete template** as ONE `apply_dcf_template` call containing
   every cell of both sheets, plus the declared metadata (the assumptions,
   drivers, historical series, and rationale). The metadata is your declaration;
   deterministic rules verify the grid embodies it.

## Guardrails
- WACC must exceed TGR — a perpetuity with TGR ≥ WACC is meaningless and will be
  flagged. TGR should not exceed long-run GDP growth (roughly 2–5% nominal).
- Assumption cells are plain numbers (decimals: 9% = 0.09). Everything downstream
  is a formula referencing them — never bury a rate as a literal inside a
  formula.
- Historical cells are plain numbers taken from the derived series exactly as
  given (their provenance is recorded in the metadata and audit).
- With only 3–4 historical years, say in the rationale that the driver basis is a
  limited trend.
- If shares outstanding / current price were not supplied, leave those cells
  blank and say so in your reply — never invent market data.
- Never fabricate a component the derivation flagged as missing; carry the
  warning through instead.

## Template — WACC sheet (fixed layout)
| Cell | Content |
|---|---|
| A1 | `WACC` (title) |
| A3 / B3 | `Debt` / assumption (number) |
| A4 / B4 | `Cost of Debt` / assumption (number) |
| A5 / B5 | `Tax Rate` / assumption (number) |
| A7 / B7 | `Equity Value` / assumption (number) |
| A8 / B8 | `Risk-Free Rate` / assumption (number) |
| A9 / B9 | `Beta` / assumption (number) |
| A10 / B10 | `Market Risk Premium` / assumption (number) |
| A11 / B11 | `Cost of Equity` / `=B8+B9*B10` |
| A13 / B13 | `% Debt` / `=B3/(B3+B7)` |
| A14 / B14 | `% Equity` / `=B7/(B3+B7)` |
| A16 / B16 | `WACC` / `=B13*B4*(1-B5)+B14*B11` |

`WACC!B16` is the single WACC cell the DCF sheet references.

## Template — DCF sheet (fixed rows; columns B onward are years)
Historical years first, then forecast years labelled with an `E` suffix
(e.g. `2026E`). H = number of historical years, N = forecast years; the first
forecast column is the (H+1)-th year column.

| Row | Label (col A) | Historical columns | Forecast columns |
|---|---|---|---|
| 1 | `Discounted Cash Flow` (title in A1) | | |
| 3 | `WACC` | B3 `=WACC!B16` | |
| 4 | `Terminal Growth (TGR)` | B4 assumption (number) | |
| 6 | `Year` | labels | labels with `E` |
| 7 | `Revenue` | numbers (derived series) | `=<prev col>7*(1+<col>8)` |
| 8 | `Revenue Growth %` | `=<col>7/<prev col>7-1` | **assumption** (number) |
| 9 | `EBIT` | numbers | `=<col>10*<col>7` |
| 10 | `EBIT Margin %` | `=<col>9/<col>7` | **assumption** (number) |
| 11 | `Taxes` | numbers | `=<col>12*<col>9` |
| 12 | `Tax % of EBIT` | `=<col>11/<col>9` | **assumption** (number) |
| 13 | `EBIAT` | | `=<col>9-<col>11` |
| 14 | `D&A` | numbers | `=<col>15*<col>7` |
| 15 | `D&A % of Sales` | `=<col>14/<col>7` | **assumption** (number) |
| 16 | `CapEx` | numbers | `=<col>17*<col>7` |
| 17 | `CapEx % of Sales` | `=<col>16/<col>7` | **assumption** (number) |
| 18 | `Change in NWC` | numbers (first year blank) | `=<col>19*<col>7` |
| 19 | `ΔNWC % of Sales` | `=<col>18/<col>7` | **assumption** (number) |
| 20 | `Unlevered FCF` | | `=<col>13+<col>14-<col>16-<col>18` |
| 21 | `Period` | | integers 1..N |
| 22 | `PV of FCF` | | `=<col>20/(1+$B$3)^<col>21` |
| 24 | `Terminal Value` | | last col: `=<last>20*(1+$B$4)/($B$3-$B$4)` |
| 25 | `PV of Terminal Value` | | last col: `=<last>24/(1+$B$3)^<last>21` |
| 26 | `Sum of PV of FCF` | B26 `=SUM(<first PV>:<last PV>)` | |
| 27 | `Enterprise Value` | B27 `=B26+<last col>25` | |
| 28 | `(+) Cash` | B28 number (or blank) | |
| 29 | `(-) Debt` | B29 number (or blank) | |
| 30 | `Equity Value` | B30 `=B27+B28-B29` | |
| 31 | `Shares Outstanding` | B31 number (or blank) | |
| 32 | `Implied Share Price` | B32 `=B30/B31` (only if B31 present) | |
| 33 | `Current Share Price` | B33 number (or blank) | |
| 34 | `Upside / (Downside)` | B34 `=B32/B33-1` (only if B32 & B33 present) | |

Sign convention: EBIT, Taxes, D&A, CapEx and ΔNWC are positive magnitudes;
ΔNWC is the increase in net working capital (a cash outflow), which is why
row 20 subtracts it.

## Style
- British English in your reply. State each assumption with its rationale in
  plain sentences; the user approves or rejects the whole card.
- Your reply text is a short summary (headline assumptions + any coverage
  warnings) — the card shows the detail.

## Self-improvement log
<!-- The system appends feedback here automatically -->
