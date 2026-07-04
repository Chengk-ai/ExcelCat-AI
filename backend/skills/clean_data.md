# Skill: Clean Data
version: 3
last_updated: 2026-07-03

## Instructions
- Look at the selected data and list every data quality issue you find
- For each issue state: the column or cell reference, the problem, and the Excel formula to fix it (wrapped in backticks)
- Reference actual cell addresses from the context — never use placeholders like "the date column"
- Issues to look for include but are not limited to: inconsistent formatting (dates, currencies, capitalisation), extra whitespace, mixed case, blank cells, duplicate rows
- If a blank cell cannot be auto-filled, flag it for manual review rather than guessing
- If you find no issues, say "No issues found."

## Data profile (authoritative counts)
A deterministic data profile is supplied alongside the data. Its blank-cell
and duplicate-row counts are computed over the FULL selection and are
authoritative — use them to direct attention (e.g. which column has blanks),
then reference the actual cells you can see. If the profile reports issues in
rows you cannot see, say they exist beyond the visible sample rather than
guessing their contents.

## Examples of good output
- A3 has mixed case "roti king" → use `=PROPER(A3)`
- B5 is blank → flag for manual review
- C2:C20 dates inconsistent (some DD/MM, some MM/DD) → standardise with `=TEXT(C2,"DD/MM/YYYY")`
- D7 has trailing whitespace "London " → use `=TRIM(D7)`

## Self-improvement log
<!-- The system appends feedback here automatically -->