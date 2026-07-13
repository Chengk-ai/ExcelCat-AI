import re
from typing import Any, List, Optional

from .base import Rule, RuleResult

_CELL_REF_RE = re.compile(r'(?<![A-Za-z])([A-Za-z]+\d+)(?!\d)')


def _abs_cell(col_offset: int, row_offset: int, range_start: str) -> Optional[str]:
    """Convert a 0-based (col, row) offset within the selection to an absolute A1 address."""
    start_match = re.match(r'^([A-Za-z]+)(\d+)$', range_start.strip())
    if not start_match:
        return None
    start_col_str = start_match.group(1).upper()
    start_row = int(start_match.group(2))

    start_col_idx = 0
    for ch in start_col_str:
        start_col_idx = start_col_idx * 26 + (ord(ch) - ord('A') + 1)

    abs_col_idx = start_col_idx + col_offset
    abs_row = start_row + row_offset

    if abs_col_idx < 1:
        return None

    letters = []
    n = abs_col_idx
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        letters.append(chr(65 + remainder))
    col_str = ''.join(reversed(letters))

    return f"{col_str}{abs_row}"


class OverwriteReferencedCellRule(Rule):
    id = "overwrite_referenced_cell"
    level = "warning"

    def applies_to(self, tool_name: str) -> bool:
        # apply_cleaning overwrites existing values, so a fix that breaks a
        # dependent formula (e.g. a VLOOKUP key) is exactly what this catches.
        return tool_name in ("write_to_cell", "apply_formula_pattern", "apply_cleaning")

    def check(self, tool_call: dict, context: Any) -> List[RuleResult]:
        if context is None:
            return []

        formulas = getattr(context, "formulas", [])
        address = getattr(context, "address", "")
        if not formulas or not address:
            return []

        range_start = address.split(":")[0]

        args = tool_call.get("args", {})
        name = tool_call.get("name", "")

        if name == "write_to_cell":
            targets = {args.get("cell", "").strip().upper()}
        elif name in ("apply_formula_pattern", "apply_cleaning"):
            targets = {str(c).strip().upper() for c in args.get("cells", [])}
        else:
            return []

        targets.discard("")

        if not targets:
            return []

        dependents = self._find_dependents(targets, formulas, range_start)
        if not dependents:
            return []

        dep_list = ", ".join(sorted(dependents)[:5])
        suffix = f" (and {len(dependents) - 5} more)" if len(dependents) > 5 else ""
        target_str = ", ".join(sorted(targets)[:3])
        return [RuleResult(
            rule_id=self.id,
            level=self.level,
            message=(
                f"Cell(s) {target_str} are referenced by formula(s) in {dep_list}{suffix}. "
                f"Overwriting may break dependent calculations."
            ),
        )]

    def _find_dependents(
        self,
        targets: set,
        formulas: List[List[Any]],
        range_start: str,
    ) -> set:
        """Return the set of cell addresses within the selection that reference any target."""
        dependents = set()
        for r_idx, row in enumerate(formulas):
            for c_idx, cell_formula in enumerate(row):
                if not isinstance(cell_formula, str) or not cell_formula.startswith("="):
                    continue
                source = _abs_cell(c_idx, r_idx, range_start)
                if not source or source in targets:
                    continue
                refs = {r.upper() for r in _CELL_REF_RE.findall(cell_formula)}
                if refs & targets:
                    dependents.add(source)
        return dependents
