from typing import List

from ..base import ReviewRule, ReviewContext, RuleResult
from .param_locator import locate_all


# Nominal long-term GDP growth in developed economies sits around 4–5%
# (inflation + real growth). A terminal growth rate above this is the
# actually-aggressive case worth questioning — common 3–4% TGRs are
# defensible and shouldn't trip a suggestion. Real real-GDP-only ceilings
# (~3%) fire too often to be useful in practice.
_GDP_UPPER = 0.05


class TgrVsGdpRule(ReviewRule):
    id = "tgr_vs_gdp"
    level = "suggestion"

    def check(self, review_ctx: ReviewContext) -> List[RuleResult]:
        tgrs = locate_all("tgr", review_ctx.values, review_ctx.address)

        results = []
        for t in tgrs:
            if t.value > _GDP_UPPER:
                pct = f"{t.value * 100:.1f}%"
                loc = f" at {t.cell}" if t.cell else ""
                results.append(RuleResult(
                    rule_id=self.id,
                    level=self.level,
                    message=(
                        f"Terminal growth rate = {pct}{loc}, above the typical "
                        f"nominal GDP growth bound (~5%). Perpetual growth this "
                        f"high implies the firm outpaces the wider economy "
                        f"forever — please confirm."
                    ),
                ))
        return results
