# Skill: Clean Data
version: 7
last_updated: 2026-07-05

## Purpose
Propose cleaning fixes for the selected data as ONE reviewable action
(apply_cleaning). Fixes are applied only after the user approves them on the
approval card — never silently. Only mechanical text fixes are actioned;
anything needing judgement or restructuring is reported as advice instead.

## Actionable fixes (the ONLY things allowed in apply_cleaning)
Each fix's fix_type must come from this exact vocabulary:
- **trim** — remove leading/trailing whitespace and collapse runs of internal
  whitespace ("  London  " → "London").
- **case_title** — title-case a name ("roti king" → "Roti King"). Two forms
  are accepted and verified: every word capitalised (Excel PROPER, "Cost Of
  Sales"), or editorial title case where exactly these minor words stay
  lower-case when not the first or last word: a, an, and, as, at, by, for,
  in, of, on, or, the, to, with ("Cost of Sales"). Prefer the editorial form
  for financial line items.
- **case_upper** / **case_lower** — force the case where the column's
  convention clearly demands it.

Rules:
- old_value must be copied EXACTLY as it appears in the data context — it is
  verified against the sheet character for character; any difference is
  flagged on the approval card.
- new_value must be EXACTLY the result of applying the declared fix_type to
  old_value — this is verified deterministically; any extra change is flagged.
- Only fix cells you can actually see in the data context. If the data
  profile reports issues beyond the visible rows, mention them in notes.
- Do NOT title-case values that may be proper nouns or brands with internal
  capitals ("iPhone", "McDonald's") — case_title would corrupt them; put them
  in notes for the user to decide.
- At most 50 fixes per proposal. If there are more, fix the worst and say so
  in notes.

## Advisory findings (notes — NEVER actioned)
- Blank cells: flag for manual review; never invent content.
- Duplicate rows: report the count and locations; deleting rows restructures
  the sheet and is not offered as an action.
- Inconsistent date formats: DD/MM vs MM/DD is genuinely ambiguous — suggest
  a target format in notes, do not convert values.
- Currency symbols or thousands separators in numeric columns: converting
  text to numbers changes the cell's type; report it, do not do it.

## Data profile (authoritative counts)
A deterministic data profile is supplied alongside the data. Its blank-cell
and duplicate-row counts are computed over the FULL selection and are
authoritative — use them for the counts in your notes rather than counting
rows yourself.

## Output
Call apply_cleaning exactly once: cells, old_values, new_values, fix_types
(four aligned arrays) plus notes for everything advisory. notes is a LIST of
findings — one finding per item, plain English, no markdown, no headings.
Each finding must be CONCRETE: name the cell(s), quote the offending value,
state what is wrong, and where the correction is obvious say what it likely
should be — e.g. 'A11 contains "justed EBITDA", likely missing an initial
"A" ("Adjusted EBITDA")'. Never compress findings into vague statements like
"appears to be truncated". If nothing is actionable, make NO tool call —
reply "No actionable issues found." followed by the findings.

## Self-improvement log
<!-- The system appends feedback here automatically -->
