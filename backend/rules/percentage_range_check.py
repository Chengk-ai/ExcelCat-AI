import re
from typing import Any, List

from .base import Rule, RuleResult
from .header_detect import get_column_type

_COL_RE = re.compile(r'^([A-Za-z]+)\d+$')


def _column_index(address: str) -> int:
    m = _COL_RE.match(address.strip())
    if not m:
        return -1
    col_str = m.group(1).upper()
    idx = 0
    for ch in col_str:
        idx = idx * 26 + (ord(ch) - ord('A') + 1)
    return idx - 1


def _infer_scale(values: List[List[Any]], local_col: int) -> str:
    """
    Look at existing column values (excluding header) to infer whether
    percentages are stored as 0-1 or 0-100.

    Returns "decimal" (0-1 system) or "whole" (0-100 system).
    If not enough data, defaults to "whole".
    """
    nums = []
    for row in values[1:]:
        if local_col < len(row):
            v = row[local_col]
            if isinstance(v, (int, float)):
                nums.append(v)
    if not nums:
        return "whole"
    if all(abs(n) <= 1 for n in nums):
        return "decimal"
    return "whole"


class PercentageRangeCheckRule(Rule):
    id = "percentage_range_check"
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
        target_col = _column_index(target)
        start_col = _column_index(range_start)
        if target_col < 0 or start_col < 0:
            return []

        local_col = target_col - start_col
        if local_col < 0:
            return []

        col_type = get_column_type(local_col, values)
        if col_type != "percentage":
            return []

        try:
            num = float(value)
        except (ValueError, TypeError):
            return [RuleResult(
                rule_id=self.id,
                level=self.level,
                message=f"Column header suggests a percentage, but \"{value}\" is not numeric.",
            )]

        scale = _infer_scale(values, local_col)

        if scale == "decimal":
            if num < -1 or num > 1:
                return [RuleResult(
                    rule_id=self.id,
                    level=self.level,
                    message=f"Column uses decimal percentages (0–1), but value {num} is outside that range.",
                )]
        else:
            if num < -100 or num > 100:
                return [RuleResult(
                    rule_id=self.id,
                    level=self.level,
                    message=f"Column uses whole-number percentages (0–100), but value {num} is outside that range.",
                )]

        return []
