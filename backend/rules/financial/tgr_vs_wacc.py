from typing import List

from ..base import ReviewRule, ReviewContext, RuleResult
from .param_locator import locate_all


def _fmt(p) -> str:
    pct = f"{p.value * 100:.1f}%"
    return f"{pct} at {p.cell}" if p.cell else pct


class TgrVsWaccRule(ReviewRule):
    id = "tgr_vs_wacc"
    level = "warning"

    def check(self, review_ctx: ReviewContext) -> List[RuleResult]:
        tgrs = locate_all("tgr", review_ctx.values, review_ctx.address)
        waccs = locate_all("wacc", review_ctx.values, review_ctx.address)

        if not tgrs or not waccs:
            return []

        results = []
        for t in tgrs:
            for w in waccs:
                if t.value >= w.value:
                    results.append(RuleResult(
                        rule_id=self.id,
                        level=self.level,
                        message=(
                            f"Terminal growth rate (TGR={_fmt(t)}) ≥ discount rate "
                            f"(WACC={_fmt(w)}). Gordon Growth model denominator is zero "
                            f"or negative — terminal value is meaningless."
                        ),
                    ))
        return results
