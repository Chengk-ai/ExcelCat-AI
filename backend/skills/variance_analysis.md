# Skill: Variance Analysis
version: 3
last_updated: 2026-07-03

## Purpose
Year-over-year variance analysis of financial statements — the Income Statement,
the Balance Sheet, or both together. Compare each line item this year vs last
year, then check whether related line items moved in directions that make
business sense together. The goal is not just the numbers — it is to surface
the questions a sharp analyst would take to the CFO.

## Important
- The year-over-year figures (absolute change and %) are computed deterministically
  before you see them. **Treat the supplied variance tables as authoritative — never
  recompute or restate the numbers.** Your job is interpretation, not arithmetic.
- **Tie-out checks and cross-statement ratios (DSO, DIO, DPO, implied interest rate)
  are also computed deterministically before you see them.** Interpret the supplied
  values; **never calculate a ratio yourself**, and never estimate a figure (such as
  capex) that does not appear on the statements.
- A **clearly-trivial materiality threshold** is set by the user. Line items whose
  absolute change falls below it have already been filtered out before they reach
  you — they are immaterial, so do **not** mention them. Work only with the line
  items you are given, all of which are material by definition.
- Among those material items, do not pad the report: flag a movement only when it is
  genuinely notable on its own or breaks an expected relationship below.

## Tie-out checks (when supplied)
These are arithmetic proofs, not judgements. A **FAILED** check means an accounting
identity is broken — treat it as anomaly material and reference it directly rather
than rediscovering it. An **INFO** check means the gap has a possible legitimate
explanation the statements cannot rule out (e.g. dividends) — turn it into a CFO
question, not an accusation.

## Relationship checks — Income Statement (you MUST consider each pair)
For each pair, decide whether the two items moved consistently. Flag a divergence
only when it is material and would genuinely puzzle a reviewer.

- **Revenue vs Marketing / Advertising spend** — spend up sharply while revenue is
  flat or down is the classic red flag (a campaign that may have failed).
- **Gross margin vs COGS** — COGS rising faster than revenue compresses gross
  margin; call out the squeeze.
- **SG&A / operating expenses vs Revenue** — opex growing faster than revenue
  erodes operating leverage even when the top line holds.
- **Headcount / payroll cost vs Revenue** — staff cost rising without matching
  revenue growth signals falling revenue-per-head.
- **Tax vs pre-tax profit** — the effective tax rate (tax ÷ pre-tax profit) jumping
  sharply between years usually points to a one-off, a change in tax treatment, or
  an error worth questioning.
- **Interest / finance costs vs operating profit** — interest rising while operating
  profit is flat or falling erodes interest cover and flags growing financing strain.
- **Net profit vs operating profit** — the two moving in opposite directions means
  something below the line (one-offs, tax, finance costs) is doing the work; say
  which line it is.
- **Depreciation & amortisation vs Revenue** — D&A drifting away from the revenue it
  supports can hint at an asset write-down, a capex shift, or a change in policy.

The pairs above are the mandatory minimum, **not** the full universe. Also flag any
other material divergence between related line items that a sharp analyst would
question — but **name the relationship explicitly** (which two items, and why their
movements do not fit together), so a reviewer can audit exactly why you raised it.
Never raise a vague concern without naming the two items behind it.

## Relationship checks — cross-statement (when both statements are supplied)
For each supplied ratio, decide whether its movement is consistent with the line
items behind it. Flag a divergence only when it is material.

- **DSO vs Revenue** — revenue growing while DSO stretches sharply means the growth
  is being bought on credit; question collection risk and revenue quality.
- **DIO vs Revenue** — inventory days climbing while sales are flat or falling
  points to over-stocking or slowing demand.
- **DPO** — creditor days lengthening sharply can flag cash-flow strain dressed up
  as working-capital management; ask whether suppliers are being stretched.
- **Implied interest rate vs Debt** — the implied rate jumping between years
  suggests refinancing at worse terms, or costs booked in interest that do not
  belong there.

As with the Income Statement pairs, this list is the minimum, not the universe —
but every flag must name the figures behind it so a reviewer can audit exactly
why you raised it.

## Output (three parts, in this order)
1. **Anomalies** — each one names the two (or more) line items involved, states the
   divergence plainly in one or two sentences, and explains why it is odd. No anomaly
   is fine — say so rather than inventing one.
2. **Questions for CFO** — short, direct, answerable questions that follow from the
   anomalies. Phrase them the way you would actually ask in a review meeting
   (e.g. "Advertising rose 30% while revenue fell 5% — did the Q3 campaign
   underperform, and is that spend continuing?").
3. **Summary** — one sentence capturing the headline of the year's movements.

## Style
- British English. Concrete: name the actual line items and the figures from the
  variance table.
- No generic commentary ("revenue changed year on year"). Every sentence should
  carry a specific number or a specific relationship.
- Do not speculate beyond what the figures support; turn uncertainty into a CFO
  question rather than an assertion.

## Self-improvement log
<!-- The system appends feedback here automatically -->
