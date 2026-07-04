from typing import List

from ..base import ReviewRule, ReviewContext, RuleResult
from .param_locator import locate_all, is_hardcoded


class WaccHardcodedRule(ReviewRule):
    """Flag a WACC that is typed in as a constant rather than computed.

    WACC is a DERIVED quantity — weights × costs of equity/debt — so audit
    convention expects a formula: a hardcoded 9.5% cannot be traced to its
    inputs and silently goes stale when they change. Primitive assumptions
    (TGR, beta, tax rate, weights) are legitimately typed in, so this rule
    deliberately checks WACC only — flagging every typed assumption would be
    noise that erodes trust in the review.
    """

    id = "wacc_hardcoded"
    level = "suggestion"

    def check(self, review_ctx: ReviewContext) -> List[RuleResult]:
        results = []
        for w in locate_all("wacc", review_ctx.values, review_ctx.address):
            if is_hardcoded(review_ctx.formulas, w) is True:
                pct = f"{w.value * 100:.1f}%"
                loc = f" at {w.cell}" if w.cell else ""
                results.append(RuleResult(
                    rule_id=self.id,
                    level=self.level,
                    message=(
                        f"WACC = {pct}{loc} is typed in as a constant rather than "
                        f"computed. Consider deriving it from its components "
                        f"(cost of equity/debt × weights) so the rate updates when "
                        f"its inputs change and a reviewer can trace where it "
                        f"came from."
                    ),
                ))
        return results
