"""
Deterministic cross-statement ratio layer (dual-statement mode only).

Pure compute — no LLM, no audit, no Excel. Every ratio here divides one located
cell by another located cell: the LLM (Pass A) only said WHERE the rows are,
the arithmetic happens on the real grid values. This preserves the variance
invariant — the LLM never produces a figure — for ratios too: writing the
BS↔IS relationships into the contract and letting the interpretation pass
"work out" DSO in its head would put un-auditable numbers in the report.

Ratio set (agreed for Phase 2):
  dso  — Receivables ÷ Revenue × 365      (debtor days)
  dio  — Inventory ÷ COGS × 365           (inventory days)
  dpo  — Payables ÷ COGS × 365            (creditor days)
  implied_interest_rate — Interest expense ÷ closing Debt

Implied capex (ΔPP&E + D&A) is deliberately excluded: capex is not a line on
either statement, so the figure would rest on assumptions (no disposals, no
impairments) rather than on cells. It returns as a real located line when the
Cash Flow statement is supported.

Notes on basis choices (stated, so they're auditable):
- Inputs are taken as absolute values: expense rows are negative in many
  layouts, and a ratio's sign carries no meaning here.
- The implied interest rate uses each year's CLOSING debt, not the average:
  with only two balance-sheet years there is no opening balance for the prior
  year, and using a consistent basis for both years keeps the delta meaningful.

A ratio whose inputs can't be located (or whose denominator is zero) is
returned with status "skipped" and the reason — shown in the UI, never
silently dropped.
"""
from typing import Any, List, Optional

from ties import _role_rows, _cell


# (id, label, basis, numerator (statement, role), denominator (statement, role), multiplier, unit)
_RATIO_DEFS = [
    ("dso", "Debtor days (DSO)", "Receivables ÷ Revenue × 365",
     ("BS", "receivables"), ("IS", "revenue"), 365.0, "days"),
    ("dio", "Inventory days (DIO)", "Inventory ÷ COGS × 365",
     ("BS", "inventory"), ("IS", "cogs"), 365.0, "days"),
    ("dpo", "Creditor days (DPO)", "Payables ÷ COGS × 365",
     ("BS", "payables"), ("IS", "cogs"), 365.0, "days"),
    ("implied_interest_rate", "Implied interest rate", "Interest expense ÷ closing Debt",
     ("IS", "interest_expense"), ("BS", "debt"), 1.0, "pct"),
]


def compute_ratios(
    is_mapping: dict,
    is_grid: List[List[Any]],
    bs_mapping: dict,
    bs_grid: List[List[Any]],
) -> List[dict]:
    """Compute the cross-statement ratios for both years. Returns a list of:

        {id, label, basis, unit, status: "ok"|"skipped",
         prior, current, delta,             # ok only; None when a year is missing
         inputs: {role: {"statement": "IS"|"BS", "row": int}},
         reason}                            # skipped only

    `inputs` records exactly which rows fed each ratio, so every figure in the
    ratio table is traceable back to cells — same audit story as the variance
    table itself.
    """
    stmts = {
        "IS": (_role_rows(is_mapping), is_grid, is_mapping),
        "BS": (_role_rows(bs_mapping), bs_grid, bs_mapping),
    }
    cols: dict = {}
    for key, (_, _, mapping) in stmts.items():
        try:
            cols[key] = (int(mapping.get("prior_col")), int(mapping.get("current_col")))
        except (TypeError, ValueError):
            cols[key] = None

    out: List[dict] = []
    for rid, label, basis, num_src, den_src, mult, unit in _RATIO_DEFS:
        entry = {"id": rid, "label": label, "basis": basis, "unit": unit}

        located, missing = {}, []
        for stmt_key, role in (num_src, den_src):
            roles, _, _ = stmts[stmt_key]
            if cols[stmt_key] is None:
                missing.append(f"{stmt_key} year columns not identified")
            elif role in roles:
                located[(stmt_key, role)] = roles[role]
            else:
                missing.append(f"{role.replace('_', ' ')} row not located on the {stmt_key}")
        if missing:
            out.append({**entry, "status": "skipped", "reason": "; ".join(missing)})
            continue

        # Per-year values: abs() because expense/liability rows are often
        # stored negative and ratio magnitude is what matters.
        values = {}  # (stmt, role) → (prior, current)
        for (stmt_key, role), row in located.items():
            _, grid, _ = stmts[stmt_key]
            pc, cc = cols[stmt_key]
            p, c = _cell(grid, row, pc), _cell(grid, row, cc)
            values[(stmt_key, role)] = (
                abs(p) if p is not None else None,
                abs(c) if c is not None else None,
            )

        def year_ratio(idx: int) -> Optional[float]:
            num = values[num_src][idx]
            den = values[den_src][idx]
            if num is None or den is None or den == 0:
                return None
            return num / den * mult

        prior, current = year_ratio(0), year_ratio(1)
        if prior is None and current is None:
            out.append({
                **entry, "status": "skipped",
                "reason": "non-numeric or zero input in both years",
            })
            continue

        out.append({
            **entry,
            "status": "ok",
            "prior": prior,
            "current": current,
            "delta": (current - prior) if (prior is not None and current is not None) else None,
            "inputs": {
                role: {"statement": stmt_key, "row": row}
                for (stmt_key, role), row in located.items()
            },
        })
    return out
