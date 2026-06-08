import re
from typing import Any, List

from .base import Rule, RuleResult

_CELL_REF_RE = re.compile(r'[A-Za-z]+\d+')


class CircularReferenceRule(Rule):
    id = "circular_reference"
    level = "warning"

    def applies_to(self, tool_name: str) -> bool:
        return tool_name in ("write_to_cell", "apply_formula_pattern")

    def check(self, tool_call: dict, context: Any) -> List[RuleResult]:
        args = tool_call.get("args", {})
        name = tool_call.get("name", "")

        if name == "write_to_cell":
            return self._check_single(args.get("cell", ""), args.get("value", ""))

        if name == "apply_formula_pattern":
            return self._check_pattern(
                args.get("cells", []),
                args.get("pattern", ""),
            )

        return []

    def _check_single(self, target: str, value: str) -> List[RuleResult]:
        if not value or not value.strip().startswith("="):
            return []
        target_norm = target.strip().upper()
        refs = {r.upper() for r in _CELL_REF_RE.findall(value)}
        if target_norm in refs:
            return [RuleResult(
                rule_id=self.id,
                level=self.level,
                message=f"Formula references its own cell ({target_norm}), which would create a circular reference.",
            )]
        return []

    def _check_pattern(self, cells: list, pattern: str) -> List[RuleResult]:
        if not pattern or not pattern.strip().startswith("="):
            return []
        results = []
        for cell in cells:
            row_match = re.match(r'^[A-Za-z]+(\d+)$', cell.strip())
            if not row_match:
                continue
            row = row_match.group(1)
            formula = pattern.replace("{r}", row)
            r = self._check_single(cell, formula)
            if r:
                results.extend(r)
                break
        return results
