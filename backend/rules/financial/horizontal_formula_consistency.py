"""
Horizontal formula consistency — catches a year's formula being overwritten or
having a term dropped relative to its neighbours.

For each formula row, template each cell's formula by its own column; if a
majority pattern exists, flag any cell whose template deviates.
"""
from __future__ import annotations
from collections import Counter
from typing import List

from ..base import ReviewRule, ReviewContext, RuleResult
from .col_pattern import col_template
from .param_locator import _index_to_col, _parse_top_left
from .row_classifier import row_segments


# A formula segment needs at least this many cells before a majority/dissent
# split is meaningful. With only 2 cells a 1-vs-1 disagreement is ambiguous.
# Public: main.py imports it to tally proof-of-work consistently.
MIN_DATA_CELLS = 3


class HorizontalFormulaConsistencyRule(ReviewRule):
    id = "horizontal_formula_consistency"
    level = "warning"

    def check(self, review_ctx: ReviewContext) -> List[RuleResult]:
        top_left = _parse_top_left(review_ctx.address)
        if top_left is None:
            return []

        results: List[RuleResult] = []
        formulas = review_ctx.formulas
        values = review_ctx.values

        for row_idx, frow in enumerate(formulas):
            # Each formula segment is checked independently, so a mixed row
            # (history formulas + hardcoded forecast) still gets its formula
            # run inspected instead of being skipped wholesale.
            for seg in row_segments(frow):
                if seg.type != "formula":
                    continue

                # Collect (col_idx, col_letter, template) per formula cell.
                entries = []
                for col_idx, cell in seg.cells:
                    col_letter = _index_to_col(top_left[0] + col_idx)
                    entries.append((col_idx, col_letter, col_template(cell, col_letter)))

                if len(entries) < MIN_DATA_CELLS:
                    continue

                templates = [e[2] for e in entries]
                counts = Counter(templates)
                (top_tpl, top_n), = counts.most_common(1)

                # No clear majority — bail. A tie tells us the data is
                # ambiguous, not that something is wrong.
                if top_n * 2 <= len(templates):
                    continue

                row_label = _row_label(values, row_idx)
                for col_idx, col_letter, tpl in entries:
                    if tpl == top_tpl:
                        continue
                    cell_ref = f"{col_letter}{top_left[1] + row_idx}"
                    results.append(RuleResult(
                        rule_id=self.id,
                        level=self.level,
                        message=(
                            f"{row_label}: {cell_ref} formula pattern differs from the row's "
                            f"majority pattern. Expected '{top_tpl}', got '{tpl}'. "
                            f"This usually means a year was manually overwritten and broke "
                            f"the formula's structure."
                        ),
                    ))

        return results


def _row_label(values: List[List], row_idx: int) -> str:
    """Best-effort row label: leftmost non-empty string cell, else 'row N'."""
    if row_idx < len(values):
        for cell in values[row_idx]:
            if isinstance(cell, str) and cell.strip():
                return f"Row '{cell.strip()}'"
    return f"Row {row_idx + 1}"
