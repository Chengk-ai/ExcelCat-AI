"""
Balance-sheet tie-out checks.

Pure compute — no LLM, no audit, no Excel. Given the BS structure mapping from
Pass A (which now tags line items with semantic roles) and the raw grid, this
verifies the accounting identities the statement MUST satisfy:

  balance          — Total assets = Total liabilities + Total equity (each year)
  re_rollforward   — Closing retained earnings = opening RE + net income − dividends
                     (needs the Income Statement's net income; cross-statement)

Unlike Find Outliers (an LLM judgement about values that *look* unusual), these
are arithmetic proofs with a residual: they either hold within tolerance or they
don't, and nobody can argue with the subtraction. Failures never block — they
are reported as facts and injected into the interpretation pass, consistent
with the pre_write_hook philosophy of informing the user, not overriding them.

Tolerance is the user's clearly-trivial threshold: "out of balance by more than
your own materiality level" is what counts as a fail.
"""
from typing import Any, List, Optional

from variance import _to_number


def _role_rows(mapping: dict) -> dict:
    """role → 0-based grid row, from a Pass A mapping. First occurrence wins."""
    out: dict = {}
    for item in mapping.get("line_items", []) or []:
        role = str(item.get("role", "") or "").strip()
        if not role or role == "other" or role in out:
            continue
        try:
            out[role] = int(item.get("row"))
        except (TypeError, ValueError):
            continue
    return out


def _cell(grid: List[List[Any]], row: Optional[int], col: Optional[int]) -> Optional[float]:
    """Numeric value at (row, col), or None if out of range / non-numeric."""
    if row is None or col is None:
        return None
    if not (0 <= row < len(grid)):
        return None
    row_vals = grid[row] or []
    if not (0 <= col < len(row_vals)):
        return None
    return _to_number(row_vals[col])


def _within(residual: float, lhs: float, rhs: float, tolerance: float) -> bool:
    # The float-noise floor only absorbs binary-representation error (values
    # arrive as floats from Office.js), never rounding done in the sheet itself.
    noise = 1e-9 * max(abs(lhs), abs(rhs), 1.0)
    return abs(residual) <= max(tolerance, noise)


def check_ties(
    bs_mapping: dict,
    bs_grid: List[List[Any]],
    tolerance: float = 0.0,
    is_mapping: Optional[dict] = None,
    is_grid: Optional[List[List[Any]]] = None,
) -> List[dict]:
    """Run the BS tie-out checks. Returns a list of check dicts:

        {id, label, status: "pass"|"fail"|"info"|"skipped", detail,
         cells: {role: {"row": int}}}

    `status` semantics:
      pass    — identity holds within tolerance
      fail    — identity provably broken (residual beyond tolerance)
      info    — residual beyond tolerance but a legitimate explanation exists
                that the sheets can't rule out (e.g. no dividends row located,
                so the RE gap may simply be dividends) — flagged, not accused
      skipped — required rows/statements not available; reason in `detail`

    Every value read comes straight from the grid via the mapping — the LLM
    located the rows, the arithmetic here is the proof.
    """
    checks: List[dict] = []
    roles = _role_rows(bs_mapping)
    try:
        cc = int(bs_mapping.get("current_col"))
        pc = int(bs_mapping.get("prior_col"))
    except (TypeError, ValueError):
        return [{
            "id": "balance", "label": "Assets = Liabilities + Equity",
            "status": "skipped",
            "detail": "Balance Sheet structure mapping has no valid year columns.",
            "cells": {},
        }]
    cur_label = str(bs_mapping.get("current_label", "") or "current year")
    pri_label = str(bs_mapping.get("prior_label", "") or "prior year")

    # ── Check 1: balance — Total assets = Total liabilities + Total equity ──
    # Two layouts supported: separate "Total liabilities" + "Total equity" rows,
    # or a single combined "Total liabilities and equity" row.
    ta_row = roles.get("total_assets")
    tle_row = roles.get("total_liabilities_and_equity")
    tl_row = roles.get("total_liabilities")
    te_row = roles.get("total_equity")

    if ta_row is None or (tle_row is None and (tl_row is None or te_row is None)):
        checks.append({
            "id": "balance", "label": "Assets = Liabilities + Equity",
            "status": "skipped",
            "detail": "Could not locate the Total assets and Total liabilities/equity rows.",
            "cells": {},
        })
    else:
        for col, year in ((pc, pri_label), (cc, cur_label)):
            assets = _cell(bs_grid, ta_row, col)
            if tle_row is not None:
                lande = _cell(bs_grid, tle_row, col)
                cells = {"total_assets": {"row": ta_row},
                         "total_liabilities_and_equity": {"row": tle_row}}
            else:
                tl = _cell(bs_grid, tl_row, col)
                te = _cell(bs_grid, te_row, col)
                lande = (tl + te) if (tl is not None and te is not None) else None
                cells = {"total_assets": {"row": ta_row},
                         "total_liabilities": {"row": tl_row},
                         "total_equity": {"row": te_row}}
            if assets is None or lande is None:
                checks.append({
                    "id": "balance", "label": f"Assets = Liabilities + Equity ({year})",
                    "status": "skipped",
                    "detail": f"{year}: non-numeric value in a totals row.",
                    "cells": cells,
                })
                continue
            residual = assets - lande
            ok = _within(residual, assets, lande, tolerance)
            checks.append({
                "id": "balance", "label": f"Assets = Liabilities + Equity ({year})",
                "status": "pass" if ok else "fail",
                "detail": (
                    f"{year}: assets {assets:,.0f} vs liabilities + equity {lande:,.0f}"
                    + (" — balances." if ok else f" — out of balance by {residual:+,.0f}.")
                ),
                "cells": cells,
            })

    # ── Check 2: re_rollforward — closing RE = opening RE + NI − dividends ──
    re_row = roles.get("retained_earnings")
    if re_row is None:
        checks.append({
            "id": "re_rollforward", "label": "Retained earnings roll-forward",
            "status": "skipped",
            "detail": "Could not locate a Retained earnings row on the Balance Sheet.",
            "cells": {},
        })
        return checks

    if not is_mapping or is_grid is None:
        checks.append({
            "id": "re_rollforward", "label": "Retained earnings roll-forward",
            "status": "skipped",
            "detail": "Needs the Income Statement (net income) — not available in this run.",
            "cells": {"retained_earnings": {"row": re_row}},
        })
        return checks

    is_roles = _role_rows(is_mapping)
    ni_row = is_roles.get("net_income")
    try:
        is_cc = int(is_mapping.get("current_col"))
    except (TypeError, ValueError):
        is_cc = None
    re_open = _cell(bs_grid, re_row, pc)
    re_close = _cell(bs_grid, re_row, cc)
    net_income = _cell(is_grid, ni_row, is_cc) if ni_row is not None else None

    if re_open is None or re_close is None or net_income is None:
        checks.append({
            "id": "re_rollforward", "label": "Retained earnings roll-forward",
            "status": "skipped",
            "detail": "Retained earnings (both years) or Net income not readable as numbers.",
            "cells": {"retained_earnings": {"row": re_row}},
        })
        return checks

    # Dividends rarely appear on the BS or IS face (they live in the SOCIE), so
    # a located dividends row is a bonus, not a requirement. Without one, a gap
    # can't be *proven* wrong — it may simply be dividends — so the breach
    # status is "info" (question it), not "fail" (accuse it).
    if "dividends" in roles:
        div_row = roles["dividends"]
        dividends = _cell(bs_grid, div_row, cc)
    elif "dividends" in is_roles:
        div_row = is_roles["dividends"]
        dividends = _cell(is_grid, div_row, is_cc)
    else:
        div_row, dividends = None, None

    expected = re_open + net_income - (abs(dividends) if dividends is not None else 0.0)
    residual = re_close - expected
    ok = _within(residual, re_close, expected, tolerance)
    cells = {"retained_earnings": {"row": re_row}, "net_income": {"row": ni_row, "statement": "IS"}}
    if dividends is not None:
        cells["dividends"] = {"row": div_row}

    if ok:
        status, tail = "pass", " — ties."
    elif dividends is not None:
        status, tail = "fail", f" — off by {residual:+,.0f}."
    else:
        status = "info"
        tail = (
            f" — difference of {residual:+,.0f}. No dividends row was located, so this "
            f"may be dividends or other equity movements — worth confirming."
        )
    checks.append({
        "id": "re_rollforward", "label": "Retained earnings roll-forward",
        "status": status,
        "detail": (
            f"Closing RE {re_close:,.0f} vs opening RE {re_open:,.0f} + net income "
            f"{net_income:,.0f}"
            + (f" − dividends {abs(dividends):,.0f}" if dividends is not None else "")
            + tail
        ),
        "cells": cells,
    })
    return checks
