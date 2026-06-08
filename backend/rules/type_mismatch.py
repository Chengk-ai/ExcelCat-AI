import re
from typing import Any, List, Optional

from .base import Rule, RuleResult

_COL_RE = re.compile(r'^([A-Za-z]+)\d+$')


def _column_index(address: str) -> Optional[int]:
    """Convert a cell address like 'B3' to a 0-based column index (B→1)."""
    m = _COL_RE.match(address.strip())
    if not m:
        return None
    col_str = m.group(1).upper()
    idx = 0
    for ch in col_str:
        idx = idx * 26 + (ord(ch) - ord('A') + 1)
    return idx - 1


def _row_index(address: str, range_start: str) -> Optional[int]:
    """Return the 0-based row offset of `address` within the selection starting at `range_start`."""
    addr_match = re.match(r'^[A-Za-z]+(\d+)$', address.strip())
    start_match = re.match(r'^[A-Za-z]+(\d+)$', range_start.strip())
    if not addr_match or not start_match:
        return None
    return int(addr_match.group(1)) - int(start_match.group(1))


def _is_numeric(v: Any) -> bool:
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, str):
        try:
            float(v)
            return True
        except (ValueError, TypeError):
            return False
    return False


class TypeMismatchRule(Rule):
    id = "type_mismatch"
    level = "suggestion"

    def applies_to(self, tool_name: str) -> bool:
        return tool_name == "write_to_cell"

    def check(self, tool_call: dict, context: Any) -> List[RuleResult]:
        if context is None:
            return []

        args = tool_call.get("args", {})
        target = args.get("cell", "")
        value = args.get("value", "")

        if not target or not value:
            return []
        if value.strip().startswith("="):
            return []

        values = getattr(context, "values", [])
        address = getattr(context, "address", "")
        if not values or not address:
            return []

        range_start = address.split(":")[0]
        col_idx = _column_index(target)
        row_offset = _row_index(target, range_start)
        if col_idx is None:
            return []

        start_col = _column_index(range_start)
        if start_col is None:
            return []
        local_col = col_idx - start_col
        if local_col < 0:
            return []

        column_values = []
        for r_idx, row in enumerate(values):
            if r_idx == row_offset:
                continue
            if local_col < len(row):
                v = row[local_col]
                if v is not None and v != "":
                    column_values.append(v)

        if len(column_values) < 3:
            return []

        num_count = sum(1 for v in column_values if _is_numeric(v))
        ratio = num_count / len(column_values)

        new_is_numeric = _is_numeric(value)

        if ratio >= 0.8 and not new_is_numeric:
            return [RuleResult(
                rule_id=self.id,
                level=self.level,
                message=f"Column is predominantly numeric but the new value \"{value}\" is text.",
            )]
        if ratio <= 0.2 and new_is_numeric and len(column_values) >= 3:
            return [RuleResult(
                rule_id=self.id,
                level=self.level,
                message=f"Column is predominantly text but the new value \"{value}\" is numeric.",
            )]

        return []
