"""
Hardcode trend anomaly — catches misplaced-decimal-style slips in a row of
hand-typed numbers.

For each hardcoded row, compute year-over-year percentage changes. If a single
change is both large in absolute terms (>50%) AND much larger than the row's
median change (>5x), flag it as a likely typo.

Conservative thresholds — false-positive contagion is what kills suggestion
rules. Tune later if the rule under-fires.
"""
from __future__ import annotations
from statistics import median
from typing import Any, List, Tuple

from ..base import ReviewRule, ReviewContext, RuleResult
from .param_locator import _index_to_col, _parse_top_left
from .row_classifier import row_segments


# A hardcode segment needs at least this many values to have a meaningful
# baseline median. 4 values → 3 changes.
# Public: main.py imports it to tally proof-of-work consistently.
MIN_VALUES = 4

# A pair where |prev| is below this floor has an undefined pct change; skip it.
_NEAR_ZERO = 1e-9

# Absolute and relative thresholds — both must be exceeded to flag.
_ABS_THRESHOLD = 0.5      # |pct change| > 50%
_REL_THRESHOLD = 5.0      # |pct change| > 5× the row's median |change|


class HardcodeTrendAnomalyRule(ReviewRule):
    id = "hardcode_trend_anomaly"
    level = "suggestion"

    def check(self, review_ctx: ReviewContext) -> List[RuleResult]:
        top_left = _parse_top_left(review_ctx.address)
        if top_left is None:
            return []

        results: List[RuleResult] = []
        formulas = review_ctx.formulas
        values = review_ctx.values

        for row_idx, frow in enumerate(formulas):
            # Each hardcode segment is checked independently, so a mixed row
            # (formula history + hand-typed forecast) still gets its hardcoded
            # run inspected instead of being skipped wholesale.
            for seg in row_segments(frow):
                if seg.type != "hardcode":
                    continue

                # Segment cells are already numeric non-bool by construction.
                data: List[Tuple[int, float]] = [
                    (col_idx, float(cell)) for col_idx, cell in seg.cells
                ]

                if len(data) < MIN_VALUES:
                    continue

                changes: List[Tuple[int, int, float]] = []  # (prev_col, curr_col, pct)
                for (pcol, pval), (ccol, cval) in zip(data, data[1:]):
                    if abs(pval) < _NEAR_ZERO:
                        continue
                    changes.append((pcol, ccol, (cval - pval) / pval))

                if len(changes) < 2:
                    continue

                abs_changes = [abs(c[2]) for c in changes]
                med = median(abs_changes)
                # If the segment is so flat that median is ~0, anything
                # non-trivial will look infinitely larger. Floor the median so
                # the relative check stays meaningful and we don't flame on a
                # 1% wobble in an otherwise-constant run.
                med = max(med, 0.05)

                row_label = _row_label(values, row_idx)
                for prev_col, curr_col, pct in changes:
                    if abs(pct) <= _ABS_THRESHOLD:
                        continue
                    if abs(pct) <= _REL_THRESHOLD * med:
                        continue
                    cell_ref = f"{_index_to_col(top_left[0] + curr_col)}{top_left[1] + row_idx}"
                    pct_str = f"{pct * 100:+.0f}%"
                    typical = f"±{med * 100:.0f}%"
                    results.append(RuleResult(
                        rule_id=self.id,
                        level=self.level,
                        message=(
                            f"{row_label}: {cell_ref} changed {pct_str} from the prior "
                            f"period, while other years vary by about {typical}. "
                            f"Please confirm this value (a common cause is a misplaced "
                            f"decimal point)."
                        ),
                    ))

        return results


def _row_label(values: List[List], row_idx: int) -> str:
    if row_idx < len(values):
        for cell in values[row_idx]:
            if isinstance(cell, str) and cell.strip():
                return f"Row '{cell.strip()}'"
    return f"Row {row_idx + 1}"
