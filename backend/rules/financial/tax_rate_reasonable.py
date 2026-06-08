from typing import List

from ..base import ReviewRule, ReviewContext, RuleResult
from .param_locator import locate_all


class TaxRateReasonableRule(ReviewRule):
    id = "tax_rate_reasonable"
    level = "suggestion"

    LOW = 0.10
    HIGH = 0.40

    def check(self, review_ctx: ReviewContext) -> List[RuleResult]:
        taxes = locate_all("tax", review_ctx.values, review_ctx.address)

        results = []
        for t in taxes:
            if t.value < 0 or t.value > 1:
                continue
            if t.value < self.LOW or t.value > self.HIGH:
                pct = f"{t.value * 100:.1f}%"
                loc = f" at {t.cell}" if t.cell else ""
                results.append(RuleResult(
                    rule_id=self.id,
                    level=self.level,
                    message=(
                        f"Tax rate = {pct}{loc}, outside the typical range (10%–40%). "
                        f"Please confirm the tax assumption is appropriate."
                    ),
                ))
        return results
