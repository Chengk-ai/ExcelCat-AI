from typing import List

from ..base import ReviewRule, ReviewContext, RuleResult
from .param_locator import locate_all


_LOW = 0.0
_HIGH = 3.0


class BetaRangeRule(ReviewRule):
    id = "beta_range"
    level = "suggestion"

    def check(self, review_ctx: ReviewContext) -> List[RuleResult]:
        betas = locate_all("beta", review_ctx.values, review_ctx.address)

        results = []
        for b in betas:
            if b.value < _LOW or b.value > _HIGH:
                loc = f" at {b.cell}" if b.cell else ""
                results.append(RuleResult(
                    rule_id=self.id,
                    level=self.level,
                    message=(
                        f"Beta = {b.value:.2f}{loc}, outside the typical range "
                        f"(0–3). Possible for very high-risk or highly-leveraged "
                        f"equities but unusual — please confirm the assumption."
                    ),
                ))
        return results
