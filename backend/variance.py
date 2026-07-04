"""
Deterministic year-over-year variance computation.

Pure compute — no LLM, no audit, no Excel. Given a structure mapping produced by
the analysis server's Pass A (which rows are line items, which two columns hold
the current/prior year) plus the raw used-range grid, this computes every delta
from the ACTUAL cell values. The LLM never produces a figure — it only locates
structure — so the numbers in the report and the audit trail are arithmetic, not
generation. This is the verifiability core of the variance feature.
"""
from typing import Any, Optional, List


def _to_number(v: Any) -> Optional[float]:
    """Coerce a cell value to float, or None if it isn't numeric.

    Excel usually hands us ints/floats directly, but values can also arrive as
    strings: "1,234", "(500)" (accounting negative), "£1,200", "12%". We parse
    those common shapes and give up (None) on anything else, so a label cell or a
    blank is never mistaken for a figure.
    """
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        neg = s.startswith("(") and s.endswith(")")
        if neg:
            s = s[1:-1]
        for ch in ("£", "$", "€", ","):
            s = s.replace(ch, "")
        s = s.strip()
        pct = s.endswith("%")
        if pct:
            s = s[:-1].strip()
        try:
            n = float(s)
        except ValueError:
            return None
        if pct:
            n = n / 100.0
        return -n if neg else n
    return None


def compute_variance(
    mapping: dict,
    grid_values: List[List[Any]],
    clearly_trivial: float = 0.0,
) -> dict:
    """Compute YoY variance for each located line item, read from the grid.

    `mapping` (from Pass A) shape, all indices 0-based into `grid_values`:
        {
          "current_col": int, "prior_col": int,
          "current_label": str, "prior_label": str,
          "line_items": [{"label": str, "row": int}, ...]
        }

    A row whose current or prior cell isn't numeric is skipped (recorded in
    `skipped`), never guessed. % change uses abs(prior) in the denominator so the
    sign reflects the direction of the actual movement even when prior < 0; when
    prior == 0 the % is undefined and the row is flagged "no_prior_base".

    `clearly_trivial` is an absolute materiality threshold (in the sheet's own
    units): a row whose absolute change is below it is marked `trivial=True`.
    This is the audit "clearly trivial" concept — the split is deterministic
    here, not an LLM judgement. 0 (the default) means nothing is trivial.

    Each returned row carries its 0-based grid `row` index, and the result
    carries `current_col`/`prior_col`, so every figure can be traced back to
    the actual cells (frontend click-to-highlight, tie-out cell references).

    Returns {current_label, prior_label, clearly_trivial, current_col,
    prior_col, rows, skipped[, error]}.
    """
    current_label = str(mapping.get("current_label", "") or "")
    prior_label = str(mapping.get("prior_label", "") or "")
    try:
        threshold = float(clearly_trivial or 0.0)
    except (TypeError, ValueError):
        threshold = 0.0

    try:
        cc = int(mapping.get("current_col"))
        pc = int(mapping.get("prior_col"))
    except (TypeError, ValueError):
        return {
            "current_label": current_label,
            "prior_label": prior_label,
            "clearly_trivial": threshold,
            "current_col": None,
            "prior_col": None,
            "rows": [],
            "skipped": [],
            "error": "structure mapping missing valid current/prior column indices",
        }

    rows: List[dict] = []
    skipped: List[dict] = []
    n_rows = len(grid_values)

    for item in mapping.get("line_items", []) or []:
        label = str(item.get("label", "")).strip()
        try:
            r = int(item.get("row"))
        except (TypeError, ValueError):
            continue
        if not (0 <= r < n_rows):
            continue

        row_vals = grid_values[r] or []
        cur = _to_number(row_vals[cc]) if cc < len(row_vals) else None
        prior = _to_number(row_vals[pc]) if pc < len(row_vals) else None
        if cur is None or prior is None:
            skipped.append({"label": label, "reason": "non-numeric current or prior value"})
            continue

        abs_delta = cur - prior
        if prior == 0:
            pct_delta: Optional[float] = None
            flags = ["no_prior_base"]
        else:
            pct_delta = abs_delta / abs(prior)
            flags = []

        trivial = threshold > 0 and abs(abs_delta) < threshold

        rows.append({
            "label": label,
            "row": r,
            "current": cur,
            "prior": prior,
            "abs_delta": abs_delta,
            "pct_delta": pct_delta,
            "flags": flags,
            "trivial": trivial,
        })

    return {
        "current_label": current_label,
        "prior_label": prior_label,
        "clearly_trivial": threshold,
        "current_col": cc,
        "prior_col": pc,
        "rows": rows,
        "skipped": skipped,
    }
