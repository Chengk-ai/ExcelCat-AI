import re
from typing import Any, List

from .base import Rule, RuleResult
from .header_detect import get_column_type

_COL_RE = re.compile(r'^([A-Za-z]+)\d+$')
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _column_index(address: str) -> int:
    m = _COL_RE.match(address.strip())
    if not m:
        return -1
    col_str = m.group(1).upper()
    idx = 0
    for ch in col_str:
        idx = idx * 26 + (ord(ch) - ord('A') + 1)
    return idx - 1


class EmailFormatCheckRule(Rule):
    id = "email_format_check"
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
        if col_type != "email":
            return []

        if _EMAIL_RE.match(value.strip()):
            return []

        return [RuleResult(
            rule_id=self.id,
            level=self.level,
            message=f"Column header suggests an email address, but \"{value}\" does not look like a valid email.",
        )]
