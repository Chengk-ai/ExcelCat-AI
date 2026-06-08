from typing import List

from ..base import ReviewRule, ReviewContext, RuleResult
from .param_locator import locate_all


class TaxRateRangeRule(ReviewRule):
    id = "tax_rate_range"
    level = "warning"

    def check(self, review_ctx: ReviewContext) -> List[RuleResult]:
        taxes = locate_all("tax", review_ctx.values, review_ctx.address)

        results = []
        for t in taxes:
            if t.value < 0 or t.value > 1:
                pct = f"{t.value * 100:.1f}%"
                loc = f" at {t.cell}" if t.cell else ""
                results.append(RuleResult(
                    rule_id=self.id,
                    level=self.level,
                    message=(
                        f"Tax rate = {pct}{loc}, outside the valid range (0%–100%). "
                        f"Check for a misplaced decimal point."
                    ),
                ))
        return results
