from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, List, Literal, TYPE_CHECKING

if TYPE_CHECKING:
    pass


@dataclass
class RuleResult:
    rule_id: str
    level: Literal["warning", "suggestion"]
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
