# Skill: Summarise Data
version: 3
last_updated: 2026-07-03

## Instructions
- Summarise the selected Excel data in bullet points
- Maximum 8 bullets
- Each bullet must be one short sentence
- Start with the most important insight
- Cover what the data is about, key numbers, and any obvious patterns
- If there are numbers, include min / max / average
- Include one actionable recommendation as one of the bullets

## Data profile (authoritative figures)
A deterministic data profile is supplied alongside the data: per-column counts,
blanks, min / max / mean / median / sum, and duplicate-row counts, computed by
the system over the FULL selection (the visible data rows may be truncated).
- Take every statistic you quote (min / max / average / totals) from the
  profile — never recompute them from the visible rows.
- If the visible rows and the profile disagree, the profile wins.
- If no profile is supplied, say which figures are based on the visible rows
  only.

## Examples of good output
- Total revenue: £12,400 across 5 products
- Best performer: Product A (£4,200, 34% of total)
- Worst performer: Product C (£800, 6% of total)
- Average revenue per product: £2,480
- Range: £800 to £4,200
- Recommendation: Focus on Product A and investigate why Product C underperforms

## Self-improvement log
<!-- The system appends feedback here automatically -->