from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, List, Literal, TYPE_CHECKING

if TYPE_CHECKING:
    pass


@dataclass
class RuleResult:
    rule_id: str
    # "info" is proof-of-work only: check_rules records the rule_id in
    # checks_run but surfaces no message — it exists so the audit trail can
    # tell "checked, fine" apart from "couldn't check". Rules' class-level
    # `level` defaults stay warning/suggestion; info is per-result.
    level: Literal["warning", "suggestion", "info"]
    message: str


class Rule(ABC):
    id: str
    level: Literal["warning", "suggestion"]

    @abstractmethod
    def applies_to(self, tool_name: str) -> bool:
        """Return True if this rule should run for the given tool."""

    @abstractmethod
    def check(
        self,
        tool_call: dict,
        context: Any,
    ) -> List[RuleResult]:
        """Run the check and return zero or more results."""


# ── Review Layer (on-demand, read-only checks) ─────────────────────────────────

@dataclass
class ReviewContext:
    """Data bundle passed to ReviewRule.check()."""
    values: List[List[Any]]
    formulas: List[List[Any]]
    address: str


class ReviewRule(ABC):
    id: str
    level: Literal["warning", "suggestion"]

    @abstractmethod
    def check(self, review_ctx: ReviewContext) -> List[RuleResult]:
        """Run the review check. Return empty list if preconditions not met."""
