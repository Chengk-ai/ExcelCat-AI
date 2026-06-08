from typing import List

from ..base import ReviewRule, ReviewContext, RuleResult
from .param_locator import locate_all


# 1 percentage point absolute tolerance — standard for capital-structure
# weights, which are typically reported to two decimal places.
_TOLERANCE = 0.01


def _fmt(p) -> str:
    pct = f"{p.value * 100:.1f}%"
    return f"{pct} at {p.cell}" if p.cell else pct


class DebtEquitySumRule(ReviewRule):
    id = "debt_equity_sum"
    level = "warning"

    def check(self, review_ctx: ReviewContext) -> List[RuleResult]:
        debts = locate_all("debt_weight", review_ctx.values, review_ctx.address)
        equities = locate_all("equity_weight", review_ctx.values, review_ctx.address)

        if not debts or not equities:
            return []

        results = []
        for d in debts:
            for e in equities:
                total = d.value + e.value
                if abs(total - 1.0) > _TOLERANCE:
                    diff_pp = f"{(total - 1.0) * 100:+.1f}pp"
                    results.append(RuleResult(
                        rule_id=self.id,
                        level=self.level,
                        message=(
                            f"% Debt ({_fmt(d)}) + % Equity ({_fmt(e)}) = "
                            f"{total * 100:.1f}% ({diff_pp} off 100%). "
                            f"Capital structure weights must sum to 100%."
                        ),
                    ))
        return results
