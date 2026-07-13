"""
Statement tie-out checks (Balance Sheet + Cash Flow).

Pure compute — no LLM, no audit, no Excel. Given the structure mappings from
Pass A (which tags line items with semantic roles) and the raw grids, this
verifies the accounting identities the statements MUST satisfy:

  Balance Sheet
    balance          — Total assets = Total liabilities + Total equity (each year)
    re_rollforward   — Closing retained earnings = opening RE + net income − dividends
                       (needs the Income Statement's net income; cross-statement)
  Cash Flow
    cash_rollforward — Opening cash + net change in cash = closing cash (each year)
    cf_sections_sum  — Operating + investing + financing flows = net change in cash
                       (each year)
    cash_ties_to_bs  — CF closing cash = the Balance Sheet's cash, both years
                       (cross-statement — the strongest proof that two
                       independently prepared statements describe the same books)

Net income vs operating cash flow is deliberately NOT a check: whether earnings
convert to cash is a judgement, so it lives in the ratio layer (cash conversion)
and the contract's relationship pairs. Checks are proofs; anomalies are
judgements.

Unlike Find Outliers (an LLM judgement about values that *look* unusual), these
are arithmetic proofs with a residual: they either hold within tolerance or they
don't, and nobody can argue with the subtraction. Failures never block — they
are reported as facts and injected into the interpretation pass, consistent
with the pre_write_hook philosophy of informing the user, not overriding them.

Tolerance is the user's clearly-trivial threshold: "out of balance by more than
your own materiality level" is what counts as a fail.

`run_checks` is the single entry point: give it whichever statements the run
located and it fans out to the checks those statements allow.
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


def run_checks(stmts: dict, tolerance: float = 0.0) -> List[dict]:
    """Single entry point: run every tie-out check the supplied statements allow.

    `stmts` maps statement role → (mapping, grid) for each statement whose
    Pass A mapping succeeded — any subset of {"IS", "BS", "CF"}. BS checks run
    when the BS is present, CF checks when the CF is present; the
    cross-statement legs (RE roll-forward needs the IS, the cash tie needs the
    BS) degrade to "skipped" with the reason when the counterpart is missing.
    The IS alone has no internal identity to prove, so IS-only runs return [].
    """
    is_mapping, is_grid = stmts.get("IS") or (None, None)
    bs_mapping, bs_grid = stmts.get("BS") or (None, None)
    cf_mapping, cf_grid = stmts.get("CF") or (None, None)

    checks: List[dict] = []
    if bs_mapping is not None:
        checks.extend(check_ties(bs_mapping, bs_grid, tolerance,
                                 is_mapping=is_mapping, is_grid=is_grid))
    if cf_mapping is not None:
        checks.extend(check_cash_flow(cf_mapping, cf_grid, tolerance,
                                      bs_mapping=bs_mapping, bs_grid=bs_grid))
    return checks


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


def check_cash_flow(
    cf_mapping: dict,
    cf_grid: List[List[Any]],
    tolerance: float = 0.0,
    bs_mapping: Optional[dict] = None,
    bs_grid: Optional[List[List[Any]]] = None,
) -> List[dict]:
    """Run the cash-flow tie-out checks. Same result shape and status semantics
    as check_ties. All three are identities on rows the Cash Flow statement
    itself claims to reconcile — a fail means the statement does not even agree
    with itself (or, for the cash tie, with the Balance Sheet).
    """
    checks: List[dict] = []
    roles = _role_rows(cf_mapping)
    try:
        cc = int(cf_mapping.get("current_col"))
        pc = int(cf_mapping.get("prior_col"))
    except (TypeError, ValueError):
        return [{
            "id": "cash_rollforward", "label": "Cash roll-forward",
            "status": "skipped",
            "detail": "Cash Flow structure mapping has no valid year columns.",
            "cells": {},
        }]
    cur_label = str(cf_mapping.get("current_label", "") or "current year")
    pri_label = str(cf_mapping.get("prior_label", "") or "prior year")
    years = ((pc, pri_label), (cc, cur_label))

    open_row = roles.get("opening_cash")
    close_row = roles.get("closing_cash")
    change_row = roles.get("net_change_in_cash")

    # ── Check 1: cash_rollforward — opening cash + net change = closing cash ──
    missing = [name for name, r in (("opening cash", open_row),
                                    ("net change in cash", change_row),
                                    ("closing cash", close_row)) if r is None]
    if missing:
        checks.append({
            "id": "cash_rollforward", "label": "Cash roll-forward",
            "status": "skipped",
            "detail": f"Could not locate the {', '.join(missing)} row(s) on the Cash Flow statement.",
            "cells": {},
        })
    else:
        cells = {"opening_cash": {"row": open_row},
                 "net_change_in_cash": {"row": change_row},
                 "closing_cash": {"row": close_row}}
        for col, year in years:
            opening = _cell(cf_grid, open_row, col)
            change = _cell(cf_grid, change_row, col)
            closing = _cell(cf_grid, close_row, col)
            if opening is None or change is None or closing is None:
                checks.append({
                    "id": "cash_rollforward", "label": f"Cash roll-forward ({year})",
                    "status": "skipped",
                    "detail": f"{year}: non-numeric value in an opening / net-change / closing cash row.",
                    "cells": cells,
                })
                continue
            expected = opening + change
            residual = closing - expected
            ok = _within(residual, closing, expected, tolerance)
            checks.append({
                "id": "cash_rollforward", "label": f"Cash roll-forward ({year})",
                "status": "pass" if ok else "fail",
                "detail": (
                    f"{year}: opening cash {opening:,.0f} + net change {change:+,.0f} "
                    f"vs closing cash {closing:,.0f}"
                    + (" — ties." if ok else f" — off by {residual:+,.0f}.")
                ),
                "cells": cells,
            })

    # ── Check 2: cf_sections_sum — OCF + ICF + FCF = net change in cash ──
    ocf_row = roles.get("operating_cash_flow")
    icf_row = roles.get("investing_cash_flow")
    fcf_row = roles.get("financing_cash_flow")
    missing = [name for name, r in (("operating", ocf_row),
                                    ("investing", icf_row),
                                    ("financing", fcf_row)) if r is None]
    if change_row is None:
        missing.append("net change in cash")
    if missing:
        checks.append({
            "id": "cf_sections_sum", "label": "Sections sum to net change in cash",
            "status": "skipped",
            "detail": f"Could not locate the {', '.join(missing)} row(s) on the Cash Flow statement.",
            "cells": {},
        })
    else:
        cells = {"operating_cash_flow": {"row": ocf_row},
                 "investing_cash_flow": {"row": icf_row},
                 "financing_cash_flow": {"row": fcf_row},
                 "net_change_in_cash": {"row": change_row}}
        for col, year in years:
            ocf = _cell(cf_grid, ocf_row, col)
            icf = _cell(cf_grid, icf_row, col)
            fcf = _cell(cf_grid, fcf_row, col)
            change = _cell(cf_grid, change_row, col)
            if ocf is None or icf is None or fcf is None or change is None:
                checks.append({
                    "id": "cf_sections_sum",
                    "label": f"Sections sum to net change in cash ({year})",
                    "status": "skipped",
                    "detail": f"{year}: non-numeric value in a section total or the net-change row.",
                    "cells": cells,
                })
                continue
            total = ocf + icf + fcf
            residual = total - change
            ok = _within(residual, total, change, tolerance)
            checks.append({
                "id": "cf_sections_sum",
                "label": f"Sections sum to net change in cash ({year})",
                "status": "pass" if ok else "fail",
                "detail": (
                    f"{year}: operating {ocf:+,.0f} + investing {icf:+,.0f} + financing "
                    f"{fcf:+,.0f} = {total:+,.0f} vs net change {change:+,.0f}"
                    + (" — ties." if ok else f" — off by {residual:+,.0f}.")
                ),
                "cells": cells,
            })

    # ── Check 3: cash_ties_to_bs — CF closing cash = BS cash, both years ──
    # The strongest cross-statement proof available: two independently prepared
    # statements claiming the same closing balance, verified for BOTH years.
    label = "Closing cash ties to Balance Sheet"
    if close_row is None:
        checks.append({
            "id": "cash_ties_to_bs", "label": label, "status": "skipped",
            "detail": "Could not locate a closing cash row on the Cash Flow statement.",
            "cells": {},
        })
        return checks
    if not bs_mapping or bs_grid is None:
        checks.append({
            "id": "cash_ties_to_bs", "label": label, "status": "skipped",
            "detail": "Needs the Balance Sheet (cash line) — not available in this run.",
            "cells": {"closing_cash": {"row": close_row}},
        })
        return checks

    bs_roles = _role_rows(bs_mapping)
    bs_cash_row = bs_roles.get("cash")
    try:
        bs_cc = int(bs_mapping.get("current_col"))
        bs_pc = int(bs_mapping.get("prior_col"))
    except (TypeError, ValueError):
        bs_cc = bs_pc = None
    if bs_cash_row is None or bs_cc is None:
        checks.append({
            "id": "cash_ties_to_bs", "label": label, "status": "skipped",
            "detail": ("Could not locate a Cash row on the Balance Sheet."
                       if bs_cash_row is None else
                       "Balance Sheet structure mapping has no valid year columns."),
            "cells": {"closing_cash": {"row": close_row}},
        })
        return checks

    cells = {"closing_cash": {"row": close_row},
             "cash": {"row": bs_cash_row, "statement": "BS"}}
    for cf_col, bs_col, year in ((pc, bs_pc, pri_label), (cc, bs_cc, cur_label)):
        cf_cash = _cell(cf_grid, close_row, cf_col)
        bs_cash = _cell(bs_grid, bs_cash_row, bs_col)
        if cf_cash is None or bs_cash is None:
            checks.append({
                "id": "cash_ties_to_bs", "label": f"{label} ({year})",
                "status": "skipped",
                "detail": f"{year}: closing cash (CF) or cash (BS) not readable as a number.",
                "cells": cells,
            })
            continue
        residual = cf_cash - bs_cash
        ok = _within(residual, cf_cash, bs_cash, tolerance)
        checks.append({
            "id": "cash_ties_to_bs", "label": f"{label} ({year})",
            "status": "pass" if ok else "fail",
            "detail": (
                f"{year}: closing cash {cf_cash:,.0f} (CF) vs cash {bs_cash:,.0f} (BS)"
                + (" — ties." if ok else f" — differs by {residual:+,.0f}.")
            ),
            "cells": cells,
        })
    return checks
