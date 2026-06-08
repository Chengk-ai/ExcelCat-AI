import re
from typing import Any, List

from .base import Rule, RuleResult
from .header_detect import get_column_type

_COL_RE = re.compile(r'^([A-Za-z]+)\d+$')

# Patterns that look like dates (not exhaustive, but high-precision)
_DATE_PATTERNS = [
    re.compile(r'^\d{4}[-/]\d{1,2}[-/]\d{1,2}$'),          # 2024-01-15, 2024/1/5
    re.compile(r'^\d{1,2}[-/]\d{1,2}[-/]\d{2,4}$'),        # 15-01-2024, 1/5/24
    re.compile(r'^\d{1,2}\s+\w{3,9}\s+\d{2,4}$'),          # 15 Jan 2024
    re.compile(r'^\w{3,9}\s+\d{1,2},?\s+\d{2,4}$'),        # Jan 15, 2024
    re.compile(r'^\d{4}\d{2}\d{2}$'),                        # 20240115
]


def _looks_like_date(value: str) -> bool:
    v = value.strip()
    for pat in _DATE_PATTERNS:
        if pat.match(v):
            return True
    return False


def _is_excel_serial_date(value: Any) -> bool:
    """Excel stores dates as serial numbers (1 = 1900-01-01). Reasonable range: 1 to 2958465 (9999-12-31)."""
    if isinstance(value, (int, float)):
        return 1 <= value <= 2958465
    return False


def _column_index(address: str) -> int:
    m = _COL_RE.match(address.strip())
    if not m:
        return -1
    col_str = m.group(1).upper()
    idx = 0
    for ch in col_str:
        idx = idx * 26 + (ord(ch) - ord('A') + 1)
    return idx - 1


class DateFormatCheckRule(Rule):
    id = "date_format_check"
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
        if col_type != "date":
            return []

        if _looks_like_date(value) or _is_excel_serial_date(value):
            return []

        try:
            num = float(value)
            if _is_excel_serial_date(num):
                return []
        except (ValueError, TypeError):
            pass

        return [RuleResult(
            rule_id=self.id,
            level=self.level,
            message=f"Column header suggests a date, but \"{value}\" does not look like a date value.",
        )]
