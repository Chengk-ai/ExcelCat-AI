# Skill: Forecast Data
version: 2
last_updated: 2026-06-08

## Instructions

### Choosing a method (decide in this order)
1. If the historical series is too short or unusable, do NOT fabricate a trend — see "Guardrails" below.
2. If the user states or clearly implies a growth rate (e.g. "20% a year", "grow it aggressively at 30%") → **growth_rate**.
3. If the user wants growth that eases off over time toward a steady state (e.g. "fast at first then settling", "fade to long-term growth", typical DCF revenue build) → **growth_rate** (fading variant).
4. If the user wants compounding/aggressive growth without naming a rate, or "continue the historical growth" for a tech/high-growth business → **exponential** (GROWTH or CAGR).
5. Otherwise, if the series rose by a roughly constant amount each year with no growth steer → **linear**.
A straight line CANNOT be aggressive — never answer an "aggressive" request with FORECAST.LINEAR.

### The methods and their formulas
- **linear** → `=FORECAST.LINEAR(new_x, known_ys, known_xs)` or `=TREND(known_ys, known_xs, new_x)`.
- **exponential — curve fit** → `=GROWTH(known_ys, known_xs, new_x)` (fits an exponential to the history).
- **exponential — CAGR** → project the historical compound annual growth rate forward, referencing the period-label cells so it is auditable:
  `=$I23*($I23/$E23)^((M$22-$I$22)/4)`
  (here `$I23/$E23` is the total growth 2017→2021, the 4th root annualises it, and the exponent is the number of years past 2021). Set `assumed_growth_rate` to the computed CAGR so the sanity check sees it.
- **growth_rate — constant** → chain an explicit rate off the last actual so the assumption is visible in the cell:
  `=I23*(1+0.2)`, `=M23*(1+0.2)`, `=N23*(1+0.2)` …
- **growth_rate — fading** → chain a rate that decreases each year toward a terminal rate:
  `=I23*(1+0.30)`, `=M23*(1+0.25)`, `=N23*(1+0.20)`, `=O23*(1+0.15)`, `=P23*(1+0.10)`.
  Use this for DCF-style revenue builds. Set `assumed_growth_rate` to the FIRST (highest) year's rate so the sanity check catches an unreasonable peak.

### Guardrails (edge cases)
- **Reference the period-label cell** (e.g. the year header) inside the formula rather than hard-coding the year literal, whenever a contiguous label row exists. This keeps formulas auditable and lets them collapse into one card.
- **Use only the actual contiguous history** shown in the data context. Skip blank/gap cells; do not invent periods.
- **Short series (fewer than 3 real data points):** a trend cannot be fitted reliably. Use the simplest defensible approach (a single growth assumption, or the last actual held flat) and say so in the rationale — do not present a regression as if it were robust.
- **Zero or negative values in the history:** GROWTH and CAGR require positive values and will error or mislead. Fall back to **linear** or an explicit **growth_rate**, and note why in the rationale.
- An implied annual growth outside roughly -50% to +100% per year will be flagged on the approval card for the user to confirm. Even aggressive tech growth rarely sustains above 100% per year for several years — keep projections defensible.

### Always provide
- A one-sentence **rationale** tying the chosen method (and any rate) to the stated business/industry context — this is the audit record.
- The **historical values** the forecast is based on, so the projection can be checked against where the series actually started.

## Examples of good output
- Method: `growth_rate` (fading). Cells M23:Q23 = `=I23*(1+0.30)`, `=M23*(1+0.25)`, `=N23*(1+0.20)`, `=O23*(1+0.15)`, `=P23*(1+0.10)`. assumed_growth_rate: 0.30. Rationale: "Revenue grows quickly then eases toward a sustainable rate, the standard build for a maturing tech business."
- Method: `exponential` (CAGR). Cells M23:Q23 = `=$I23*($I23/$E23)^((M$22-$I$22)/4)` …. Rationale: "Projected the 2017–2021 compound annual growth rate forward, as the user asked to continue the historical growth trajectory."
- Method: `exponential` (curve fit). Cells M23:Q23 = `=GROWTH($E$23:$I$23,$E$22:$I$22,M$22)` …. Rationale: "Exponential fit chosen for an aggressively growing tech company, where revenue compounds rather than rising by a fixed amount."
- Method: `linear`. Cells M23:Q23 = `=FORECAST.LINEAR(M$22,$E$23:$I$23,$E$22:$I$22)` …. Rationale: "Linear projection used because the series rose by a roughly constant amount each year with no growth steer."

## Self-improvement log
<!-- The system appends feedback here automatically -->
