# Skill: Find Outliers
version: 2
last_updated: 2026-07-03

## Instructions
- Identify cells or rows in the selected data that look anomalous or unusual
- For each outlier state: the cell reference, the value, and one short sentence explaining why it stands out (e.g. an order of magnitude larger than the rest, breaks a pattern, wrong type, suspicious zero)
- Base every claim on the actual data — never invent values
- If nothing looks unusual, say "No outliers found."
- Do not flag a value as an outlier just because it is the highest or lowest — there must be a substantive reason (statistical, contextual, or pattern-based)

## Data profile (authoritative candidates)
A deterministic data profile is supplied alongside the data, including
statistical outlier candidates (IQR method, computed over the FULL selection,
with cell references).
- Start from the profile's candidates: confirm or dismiss each with a
  contextual reason — a statistical outlier can still be legitimate (e.g. a
  totals row).
- You may add outliers the statistics cannot see (wrong type in a column,
  pattern breaks, suspicious zeros) — but never quote a magnitude comparison
  ("1000x larger") that the profile's figures do not support.
- If no profile is supplied, fall back to inspecting the visible rows and say
  so.

## Examples of good output
- B7 = 9,800,000 → roughly 1000x larger than other values in the column, likely a typo or wrong unit
- C12 = -50 → negative value in a column of quantities sold, which should not be possible
- A4 = "tba" → text value in an otherwise numeric column

## Self-improvement log
<!-- The system appends feedback here automatically -->
