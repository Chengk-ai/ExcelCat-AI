from typing import Any, List

from .base import Rule, RuleResult


class ForecastSanityRule(Rule):
    """Acceptance-range check for apply_forecast (Verification Layer, write-time).

    Note: the *realised* projected numbers cannot be checked here — Excel only
    evaluates the formulas after the user approves the write. So this rule
    checks the forecast's *assumption*: the explicit assumed growth rate if
    given, otherwise the historical CAGR implied by the history values. A
    figure outside the sane band is surfaced as a suggestion (never blocks —
    the hook never blocks), for the user to confirm.
    """

    id = "forecast_sanity"
    level = "suggestion"

    # Annual growth band. Even aggressive tech rarely sustains > 100%/yr.
    MAX_ANNUAL_GROWTH = 1.0   # +100%
    MIN_ANNUAL_GROWTH = -0.5  # -50%

    def applies_to(self, tool_name: str) -> bool:
        return tool_name == "apply_forecast"

    def check(self, tool_call: dict, context: Any) -> List[RuleResult]:
        args = tool_call.get("args", {}) or {}
        results: List[RuleResult] = []

        implied = None
        source = None

        # 1) Prefer an explicit assumed growth rate.
        rate = args.get("assumed_growth_rate")
        if isinstance(rate, (int, float)) and not isinstance(rate, bool):
            implied = float(rate)
            source = "assumed growth rate"
        else:
            # 2) Fall back to the historical CAGR from the history values.
            hv = args.get("history_values") or []
            nums = [
                float(v) for v in hv
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            ]
            if len(nums) >= 2 and nums[0] > 0 and nums[-1] > 0:
                periods = len(nums) - 1
                implied = (nums[-1] / nums[0]) ** (1 / periods) - 1
                source = "historical CAGR"

        if implied is None:
            return results

        if implied > self.MAX_ANNUAL_GROWTH or implied < self.MIN_ANNUAL_GROWTH:
            pct = f"{implied * 100:.1f}%"
            results.append(RuleResult(
                rule_id=self.id,
                level=self.level,
                message=(
                    f"Forecast implies an annual growth of {pct} ({source}), outside "
                    f"the typical sanity band (-50% to +100% per year). Please confirm "
                    f"this projection is intended."
                ),
            ))
        return results
