"""
Deterministic cross-statement ratio layer.

Pure compute — no LLM, no audit, no Excel. Every ratio here divides one located
cell by another located cell: the LLM (Pass A) only said WHERE the rows are,
the arithmetic happens on the real grid values. This preserves the variance
invariant — the LLM never produces a figure — for ratios too: writing the
cross-statement relationships into the contract and letting the interpretation
pass "work out" DSO in its head would put un-auditable numbers in the report.

Ratio set:
  dso  — Receivables ÷ Revenue × 365      (IS + BS; debtor days)
  dio  — Inventory ÷ COGS × 365           (IS + BS; inventory days)
  dpo  — Payables ÷ COGS × 365            (IS + BS; creditor days)
  implied_interest_rate — Interest expense ÷ closing Debt   (IS + BS)
  cash_conversion — Operating cash flow ÷ Net income        (CF + IS)
  capex_vs_da     — |Capex| ÷ |D&A|                         (CF; D&A taken from
                    the CF add-back first, IS fallback — `inputs` records which
                    row actually fed the figure)

Each definition declares which statement(s) each input may come from; a ratio
only appears in a run when every input has at least one candidate statement
present (an IS-only run returns no ratios rather than a wall of skips). Within
an applicable ratio, a row that can't be located (or a zero denominator) is
returned with status "skipped" and the reason — shown in the UI, never
silently dropped.

Notes on basis choices (stated, so they're auditable):
- Inputs are taken as absolute values: expense rows are negative in many
  layouts, and for most ratios the sign carries no meaning. The one exception
  is cash conversion, which keeps its signs — operating cash flow turning
  negative while net income stays positive is exactly the movement the ratio
  exists to expose, and abs() would hide it.
- The implied interest rate uses each year's CLOSING debt, not the average:
  with only two balance-sheet years there is no opening balance for the prior
  year, and using a consistent basis for both years keeps the delta meaningful.
- Capex vs D&A compares real located lines (this is why the ratio waited for
  Cash Flow support — an implied capex from ΔPP&E would rest on assumptions,
  not cells).
"""
from typing import Any, Dict, List, Optional, Tuple

from ties import _role_rows, _cell


# Each side ("num"/"den") is a tuple of (statement, role) candidates, tried in
# order — the first candidate whose statement is in the run AND whose role was
# located wins. A ratio is applicable only when every side has at least one
# candidate statement present in the run.
_RATIO_DEFS = [
    {
        "id": "dso", "label": "Debtor days (DSO)",
        "basis": "Receivables ÷ Revenue × 365",
        "num": (("BS", "receivables"),), "den": (("IS", "revenue"),),
        "mult": 365.0, "unit": "days", "signed": False,
    },
    {
        "id": "dio", "label": "Inventory days (DIO)",
        "basis": "Inventory ÷ COGS × 365",
        "num": (("BS", "inventory"),), "den": (("IS", "cogs"),),
        "mult": 365.0, "unit": "days", "signed": False,
    },
    {
        "id": "dpo", "label": "Creditor days (DPO)",
        "basis": "Payables ÷ COGS × 365",
        "num": (("BS", "payables"),), "den": (("IS", "cogs"),),
        "mult": 365.0, "unit": "days", "signed": False,
    },
    {
        "id": "implied_interest_rate", "label": "Implied interest rate",
        "basis": "Interest expense ÷ closing Debt",
        "num": (("IS", "interest_expense"),), "den": (("BS", "debt"),),
        "mult": 1.0, "unit": "pct", "signed": False,
    },
    {
        "id": "cash_conversion", "label": "Cash conversion",
        "basis": "Operating cash flow ÷ Net income",
        "num": (("CF", "operating_cash_flow"),), "den": (("IS", "net_income"),),
        "mult": 1.0, "unit": "x", "signed": True,
    },
    {
        "id": "capex_vs_da", "label": "Capex vs D&A",
        "basis": "Capex ÷ Depreciation & amortisation",
        "num": (("CF", "capex"),),
        "den": (("CF", "depreciation_amortisation"), ("IS", "depreciation_amortisation")),
        "mult": 1.0, "unit": "x", "signed": False,
    },
]


def compute_ratios(stmts: Dict[str, Tuple[dict, List[List[Any]]]]) -> List[dict]:
    """Compute the applicable ratios for both years. `stmts` maps statement
    role → (mapping, grid) for each statement whose Pass A mapping succeeded —
    any subset of {"IS", "BS", "CF"}. Returns a list of:

        {id, label, basis, unit, status: "ok"|"skipped",
         prior, current, delta,             # ok only; None when a year is missing
         inputs: {role: {"statement": str, "row": int}},
         reason}                            # skipped only

    `inputs` records exactly which rows fed each ratio, so every figure in the
    ratio table is traceable back to cells — same audit story as the variance
    table itself.
    """
    prepared: Dict[str, tuple] = {}
    for stmt_key, (mapping, grid) in stmts.items():
        try:
            cols = (int(mapping.get("prior_col")), int(mapping.get("current_col")))
        except (TypeError, ValueError):
            cols = None
        prepared[stmt_key] = (_role_rows(mapping), grid, cols)

    out: List[dict] = []
    for d in _RATIO_DEFS:
        # Applicability: a side none of whose candidate statements are in the
        # run means the ratio doesn't belong to this mode at all — omit it
        # entirely ("skipped" is reserved for rows missing within a mode where
        # the ratio was expected).
        if any(all(stmt_key not in prepared for stmt_key, _ in side)
               for side in (d["num"], d["den"])):
            continue
        entry = {"id": d["id"], "label": d["label"], "basis": d["basis"], "unit": d["unit"]}

        # Resolve each side to the first candidate actually located.
        sources: Dict[str, tuple] = {}
        missing: List[str] = []
        for side_name, side in (("num", d["num"]), ("den", d["den"])):
            reasons: List[str] = []
            for stmt_key, role in side:
                if stmt_key not in prepared:
                    continue
                roles, _, cols = prepared[stmt_key]
                if cols is None:
                    reasons.append(f"{stmt_key} year columns not identified")
                elif role in roles:
                    sources[side_name] = (stmt_key, role, roles[role])
                    break
                else:
                    reasons.append(f"{role.replace('_', ' ')} row not located on the {stmt_key}")
            if side_name not in sources:
                missing.extend(reasons)
        if missing:
            out.append({**entry, "status": "skipped", "reason": "; ".join(missing)})
            continue

        # Per-year values. abs() unless the definition is signed — see the
        # module docstring for the basis note.
        def side_values(chosen: tuple) -> tuple:
            stmt_key, _, row = chosen
            _, grid, cols = prepared[stmt_key]
            pc, cc = cols
            vals = (_cell(grid, row, pc), _cell(grid, row, cc))
            if d["signed"]:
                return vals
            return tuple(abs(v) if v is not None else None for v in vals)

        num_vals = side_values(sources["num"])
        den_vals = side_values(sources["den"])

        def year_ratio(idx: int) -> Optional[float]:
            num, den = num_vals[idx], den_vals[idx]
            if num is None or den is None or den == 0:
                return None
            return num / den * d["mult"]

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
                for stmt_key, role, row in sources.values()
            },
        })
    return out
