"""
Deterministic data profile for the text-skill passes (summarise / find
outliers / clean / analyse).

Why this exists: the skill pass answers from a prompt that holds at most the
first 100 rows of the selection, and an LLM "computing" a mean is generation,
not arithmetic. Same lesson as analyse_data_state (reflexion) and variance
(compute_variance): the model only respects facts that are computed for it and
put in front of it explicitly. This module computes those facts — per-column
statistics and IQR outlier candidates over the FULL selection — and renders
them as the authoritative block the skill contracts refer to.

Pure stdlib, no LLM, no audit, no Office.js. Deliberately a context builder in
main.py's orbit (like analyse_data_state), NOT an MCP capability — it has no
failure mode worth isolating and sits on the /chat hot path.
"""
from __future__ import annotations
import re
from statistics import mean, median, quantiles
from typing import Any, List, Optional, Tuple

# Rendering caps: keep the block small enough to never dominate the prompt.
MAX_PROFILE_COLS = 30
MAX_OUTLIERS_PER_COL = 8
# IQR on a handful of points is noise, not statistics.
MIN_VALUES_FOR_OUTLIERS = 8

_CELL_RE = re.compile(r"\$?([A-Za-z]+)\$?(\d+)")


def _parse_top_left(address: str) -> Optional[Tuple[int, int]]:
    """Top-left of 'Sheet1!B2:F20' → (col_idx_0based, row_1based).

    Mirrors the private helper in rules/financial/param_locator — duplicated
    (15 lines) rather than importing another module's underscore names.
    """
    bare = (address or "").split("!")[-1]
    m = _CELL_RE.match(bare)
    if not m:
        return None
    col = 0
    for ch in m.group(1).upper():
        col = col * 26 + (ord(ch) - ord("A") + 1)
    return col - 1, int(m.group(2))


def _col_letters(idx: int) -> str:
    letters = []
    idx += 1
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        letters.append(chr(rem + ord("A")))
    return "".join(reversed(letters))


def _is_blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _fmt_num(n: float) -> str:
    """Thousands separators; drop decimals when the value is integral."""
    if isinstance(n, float) and n.is_integer():
        n = int(n)
    if isinstance(n, int):
        return f"{n:,}"
    return f"{n:,.2f}"


def _detect_header(values: List[List[Any]]) -> bool:
    """Row 0 reads as labels when every non-blank cell in it is text, at least
    one label exists, and numbers appear somewhere below."""
    if len(values) < 2:
        return False
    labels = [v for v in values[0] if not _is_blank(v)]
    if not labels or not all(isinstance(v, str) for v in labels):
        return False
    return any(_is_number(v) for row in values[1:] for v in row)


def _cell_ref(origin: Optional[Tuple[int, int]], grid_row: int, grid_col: int) -> str:
    """A1-style ref when the selection origin is known, honest fallback when not."""
    if origin is None:
        return f"row {grid_row + 1}"
    return f"{_col_letters(origin[0] + grid_col)}{origin[1] + grid_row}"


def build_profile(values: List[List[Any]], address: str = "") -> str:
    """Render the authoritative profile block for a selection, or "" when
    there is nothing to profile (no selection, no data).

    The returned string is injected verbatim into the skill pass AND recorded
    verbatim in the audit trail — one artifact, two consumers, so an auditor
    sees exactly the facts the model was given.
    """
    if not values:
        return ""
    n_cols = max((len(r) for r in values), default=0)
    if n_cols == 0 or not any(not _is_blank(v) for row in values for v in row):
        return ""

    has_header = _detect_header(values)
    header = values[0] if has_header else []
    data = values[1:] if has_header else values
    data_start = 1 if has_header else 0
    if not data:
        return ""

    origin = _parse_top_left(address)

    # Grid-level facts. Duplicates = occurrences beyond the first of an
    # identical non-blank row; blank rows counted separately.
    non_blank_rows = [tuple(row) for row in data if not all(_is_blank(v) for v in row)]
    dup_rows = len(non_blank_rows) - len(set(non_blank_rows))
    blank_rows = len(data) - len(non_blank_rows)

    lines = [
        "[Data profile — computed deterministically by the system over the FULL "
        f"selection ({len(data)} data rows × {n_cols} columns"
        + (", first row read as headers" if has_header else "")
        + "). Figures here are authoritative; the visible data rows may be truncated.]",
        f"Grid: {len(data)} data rows · {dup_rows} duplicate rows · {blank_rows} blank rows",
    ]

    for c in range(min(n_cols, MAX_PROFILE_COLS)):
        col_vals = [row[c] if c < len(row) else None for row in data]
        nums = [(data_start + i, float(v)) for i, v in enumerate(col_vals) if _is_number(v)]
        texts = [v for v in col_vals if isinstance(v, str) and v.strip()]
        blanks = sum(1 for v in col_vals if _is_blank(v))

        label = _col_letters(origin[0] + c) if origin else f"Column {c + 1}"
        if has_header and c < len(header) and not _is_blank(header[c]):
            label += f' "{str(header[c]).strip()}"'

        if nums:
            xs = [v for _, v in nums]
            line = (
                f"{label} (numeric): {len(nums)} values · {blanks} blank · "
                f"min {_fmt_num(min(xs))} · max {_fmt_num(max(xs))} · "
                f"mean {_fmt_num(round(mean(xs), 2))} · median {_fmt_num(median(xs))} · "
                f"sum {_fmt_num(round(sum(xs), 2))}"
            )
            if texts:
                # Mixed types are themselves a finding (find outliers / clean).
                line += f" · {len(texts)} text value(s) mixed in"
            lines.append(line)

            if len(xs) >= MIN_VALUES_FOR_OUTLIERS:
                # method="inclusive" matches Excel's QUARTILE.INC, so a user
                # re-deriving the fences in-sheet gets the same numbers.
                q = quantiles(sorted(xs), n=4, method="inclusive")
                iqr = q[2] - q[0]
                if iqr > 0:
                    lo, hi = q[0] - 1.5 * iqr, q[2] + 1.5 * iqr
                    outs = [(r, v) for r, v in nums if v < lo or v > hi]
                    if outs:
                        shown = outs[:MAX_OUTLIERS_PER_COL]
                        refs = " · ".join(
                            f"{_cell_ref(origin, r, c)} = {_fmt_num(v)}" for r, v in shown
                        )
                        more = f" (+{len(outs) - len(shown)} more)" if len(outs) > len(shown) else ""
                        lines.append(f"  outlier candidates (IQR): {refs}{more}")
        elif texts:
            lines.append(
                f"{label} (text): {len(texts)} non-empty · {blanks} blank · "
                f"{len(set(texts))} distinct"
            )
        # all-blank columns are skipped — nothing to say about them

    if n_cols > MAX_PROFILE_COLS:
        lines.append(f"(+{n_cols - MAX_PROFILE_COLS} more columns not profiled)")

    return "\n".join(lines)
