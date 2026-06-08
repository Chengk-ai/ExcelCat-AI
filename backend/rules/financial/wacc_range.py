from typing import List

from ..base import ReviewRule, ReviewContext, RuleResult
from .param_locator import locate_all


class WaccRangeRule(ReviewRule):
    id = "wacc_range"
    level = "suggestion"

    def check(self, review_ctx: ReviewContext) -> List[RuleResult]:
        waccs = locate_all("wacc", review_ctx.values, review_ctx.address)

        results = []
        for w in waccs:
            if w.value < 0.05 or w.value > 0.15:
                pct = f"{w.value * 100:.1f}%"
                loc = f" at {w.cell}" if w.cell else ""
                results.append(RuleResult(
                    rule_id=self.id,
                    level=self.level,
                    message=(
                        f"WACC = {pct}{loc}, outside the typical range (5%–15%). "
                        f"Please confirm the discount rate assumption is appropriate."
                    ),
                ))
        return results
