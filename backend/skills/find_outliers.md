# Skill: Find Outliers
version: 1
last_updated: 2026-04-26

## Instructions
- Identify cells or rows in the selected data that look anomalous or unusual
- For each outlier state: the cell reference, the value, and one short sentence explaining why it stands out (e.g. an order of magnitude larger than the rest, breaks a pattern, wrong type, suspicious zero)
- Base every claim on the actual data — never invent values
- If nothing looks unusual, say "No outliers found."
- Do not flag a value as an outlier just because it is the highest or lowest — there must be a substantive reason (statistical, contextual, or pattern-based)

## Examples of good output
- B7 = 9,800,000 → roughly 1000x larger than other values in the column, likely a typo or wrong unit
- C12 = -50 → negative value in a column of quantities sold, which should not be possible
- A4 = "tba" → text value in an otherwise numeric column

## Self-improvement log
<!-- The system appends feedback here automatically -->
