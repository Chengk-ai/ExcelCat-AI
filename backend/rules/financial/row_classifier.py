"""
Row-type triage for projection-block rules.

A DCF projection row can be:
  - all formulas (e.g. Unlevered FCF, computed each year)
  - all hardcoded numbers (e.g. D&A pasted from management guidance)
  - mixed (history segment as formulas, forecast segment as hardcoded)
  - too sparse to compare

Text labels and empty cells are ignored. Only "data cells" — formulas or
numeric hardcodes — drive the classification.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple


RowType = Literal["formula", "hardcode", "mixed", "skip"]
CellType = Literal["formula", "hardcode"]


def classify_row(formulas_row: List[Any]) -> RowType:
    """
    Classify a row from ReviewContext.formulas.

    Office.js semantics: a formula cell returns its formula string ("=A1+B1");
    a non-formula cell returns its literal value (number / string / bool).
    So a single field is enough to split formulas from hardcodes from labels.
    """
    formulas = 0
    hardcodes = 0
    for cell in formulas_row:
        if cell is None or cell == "":
            continue
        if isinstance(cell, bool):
            # Booleans are technically numeric in Python — exclude before the
            # numeric branch so a TRUE/FALSE cell doesn't get counted as
            # hardcoded data.
            continue
        if isinstance(cell, str):
            if cell.startswith("="):
                formulas += 1
            # Plain string → text label, ignore.
        elif isinstance(cell, (int, float)):
            hardcodes += 1

    total = formulas + hardcodes
    if total < 2:
        return "skip"
    if formulas == total:
        return "formula"
    if hardcodes == total:
        return "hardcode"
    return "mixed"


def _cell_type(cell: Any) -> Optional[CellType]:
    """
    Per-cell triage: the data-type of a single cell, or None for non-data.

    Must stay in lock-step with the per-cell rules inside `classify_row`:
    booleans and plain strings and empties are non-data; "=..." is a formula;
    numbers are hardcodes. Kept as a separate primitive because `classify_row`
    is left untouched for its existing callers.
    """
    if cell is None or cell == "":
        return None
    if isinstance(cell, bool):
        return None
    if isinstance(cell, str):
        return "formula" if cell.startswith("=") else None
    if isinstance(cell, (int, float)):
        return "hardcode"
    return None


@dataclass
class Segment:
    """A run of consecutive same-type data cells within one row.

    `cells` holds (col_idx, raw_cell) pairs; len(cells) is the segment size.
    """
    type: CellType
    cells: List[Tuple[int, Any]]


def row_segments(formulas_row: List[Any]) -> List[Segment]:
    """
    Split a row into consecutive same-type data segments.

    Non-data cells (text labels, empties, booleans) are transparent: they are
    skipped over and do NOT break a run. Only a change of data-type (formula
    vs hardcode) between data cells starts a new segment.

    Transparency is deliberate and load-bearing: a pure formula row with an
    internal blank — [=A, =B, "", =D] — must stay ONE formula segment so the
    horizontal-consistency rule still sees all three cells, matching the
    behaviour `classify_row` gives today. A pure row therefore yields exactly
    one segment; a mixed row yields one segment per type-run.
    """
    segments: List[Segment] = []
    current_type: Optional[CellType] = None
    current_cells: List[Tuple[int, Any]] = []

    for col_idx, cell in enumerate(formulas_row):
        t = _cell_type(cell)
        if t is None:
            continue
        if t == current_type:
            current_cells.append((col_idx, cell))
        else:
            if current_cells:
                segments.append(Segment(current_type, current_cells))
            current_type = t
            current_cells = [(col_idx, cell)]

    if current_cells:
        segments.append(Segment(current_type, current_cells))

    return segments


def count_inspectable_cells(
    formulas_rows: List[List[Any]],
    min_formula_cells: int,
    min_hardcode_cells: int,
) -> Dict[str, int]:
    """
    Proof-of-work tally for the row-level rules: how many data cells of each
    kind actually clear the inspection bar.

    A segment only counts if it meets the same threshold its rule uses, so the
    "Checked N cells" chip never claims more than what could have been flagged.
    Thresholds are injected (not hard-coded here) to keep this decoupled from
    the rule modules that own them.
    """
    counts = {"formula": 0, "hardcode": 0}
    minimums = {"formula": min_formula_cells, "hardcode": min_hardcode_cells}
    for frow in formulas_rows:
        for seg in row_segments(frow):
            if len(seg.cells) >= minimums[seg.type]:
                counts[seg.type] += len(seg.cells)
    return counts
