"""
DCF numeric sanity — hook-time acceptance-range checks for apply_dcf_template.

Runs on the DECLARED metadata only (the sheets' formulas are unevaluated until
the user approves the write, so realised figures can't be checked here — same
constraint as ForecastSanityRule). Structural declared-vs-actual verification
of the grids is DcfIntegrityRule's job; this rule asks whether the declared
assumptions are financially sane, and whether they agree with their own
consequences via dcf.recompute_dcf — the one place the valuation arithmetic
lives, shared with the derivation layer so rule and sheet can't disagree.

Bands deliberately mirror the review-layer rules (WaccRangeRule 5–15%,
TgrVsGdpRule ≤5%, BetaRangeRule 0–3, TaxRateRangeRule) so an assumption that
would be flagged in an on-demand review is flagged the same way at write time.
"""
from typing import Any, List, Optional

from ..base import Rule, RuleResult
from dcf import recompute_dcf

# Shared bands (see module docstring for the review-layer counterparts).
WACC_LOW, WACC_HIGH = 0.05, 0.15
TGR_LOW, TGR_HIGH = 0.0, 0.05
TAX_LOW, TAX_HIGH = 0.15, 0.35
BETA_LOW, BETA_HIGH = 0.0, 3.0
TV_SHARE_WARN = 0.75
# Declared WACC vs its own CAPM components: beyond rounding noise means the
# model wrote one number on the sheet and used another in its head.
WACC_RECONCILE_TOL = 0.005


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


class DcfSanityRule(Rule):
    id = "dcf_sanity"
    level = "suggestion"

    def applies_to(self, tool_name: str) -> bool:
        return tool_name == "apply_dcf_template"

    def check(self, tool_call: dict, context: Any) -> List[RuleResult]:
        args = tool_call.get("args", {}) or {}
        results: List[RuleResult] = []

        wacc = _num(args.get("wacc"))
        tgr = _num(args.get("terminal_growth"))
        comp = args.get("wacc_components") or {}

        # ── The identity the whole model rests on: WACC > TGR ──
        if wacc is not None and tgr is not None and wacc <= tgr:
            results.append(RuleResult(
                rule_id="dcf_wacc_vs_tgr", level="warning",
                message=(
                    f"WACC ({wacc * 100:.1f}%) is not above the terminal growth rate "
                    f"({tgr * 100:.1f}%) — the Gordon terminal value is undefined or "
                    f"negative. The valuation cannot stand as declared."
                ),
            ))
        if wacc is not None and wacc <= 0:
            results.append(RuleResult(
                rule_id="dcf_wacc_nonpositive", level="warning",
                message=(
                    f"WACC = {wacc * 100:.1f}% — discount factors will not decrease "
                    f"over time, so later cash flows would be worth more than "
                    f"earlier ones."
                ),
            ))

        # ── Range bands (mirroring the review-layer rules) ──
        if wacc is not None and not (WACC_LOW <= wacc <= WACC_HIGH):
            results.append(RuleResult(
                rule_id="dcf_wacc_range", level="warning",
                message=(
                    f"WACC = {wacc * 100:.1f}%, outside the typical range "
                    f"({WACC_LOW * 100:.0f}%–{WACC_HIGH * 100:.0f}%). Please confirm "
                    f"the discount rate assumption."
                ),
            ))
        if tgr is not None and not (TGR_LOW <= tgr <= TGR_HIGH):
            results.append(RuleResult(
                rule_id="dcf_tgr_range", level="warning",
                message=(
                    f"Terminal growth rate = {tgr * 100:.1f}%, outside the typical "
                    f"range ({TGR_LOW * 100:.0f}%–{TGR_HIGH * 100:.0f}% nominal). "
                    f"Perpetual growth above long-run GDP implies the firm outpaces "
                    f"the economy forever."
                ),
            ))
        tax = _num(comp.get("tax_rate"))
        if tax is not None and not (TAX_LOW <= tax <= TAX_HIGH):
            results.append(RuleResult(
                rule_id="dcf_tax_range", level="suggestion",
                message=(
                    f"Tax rate = {tax * 100:.1f}%, outside the typical corporate "
                    f"range ({TAX_LOW * 100:.0f}%–{TAX_HIGH * 100:.0f}%). Please "
                    f"confirm."
                ),
            ))
        beta = _num(comp.get("beta"))
        if beta is not None and not (BETA_LOW <= beta <= BETA_HIGH):
            results.append(RuleResult(
                rule_id="dcf_beta_range", level="suggestion",
                message=(
                    f"Beta = {beta:.2f}, outside the typical range "
                    f"({BETA_LOW:.0f}–{BETA_HIGH:.0f}). Possible for very high-risk "
                    f"equities but unusual — please confirm."
                ),
            ))

        # ── Declared vs recomputed (dcf.recompute_dcf re-runs the arithmetic) ──
        recomputed = recompute_dcf(args)

        capm = recomputed.get("capm_wacc")
        if wacc is not None and capm is not None and abs(wacc - capm) > WACC_RECONCILE_TOL:
            results.append(RuleResult(
                rule_id="dcf_wacc_reconcile", level="warning",
                message=(
                    f"Declared WACC ({wacc * 100:.2f}%) does not match the WACC its "
                    f"own CAPM components produce ({capm * 100:.2f}%). The sheet's "
                    f"WACC formula and the declared assumption disagree."
                ),
            ))

        tv_share = recomputed.get("tv_share_of_ev")
        if tv_share is not None and tv_share > TV_SHARE_WARN:
            results.append(RuleResult(
                rule_id="dcf_tv_dominance", level="suggestion",
                message=(
                    f"The terminal value contributes {tv_share * 100:.0f}% of "
                    f"enterprise value (above {TV_SHARE_WARN * 100:.0f}%) — the "
                    f"valuation rests mostly on the perpetuity assumptions rather "
                    f"than the explicit forecast. Consider a longer forecast period "
                    f"or revisiting WACC/TGR."
                ),
            ))

        if not results:
            # Proof-of-work: everything checked and in band.
            results.append(RuleResult(rule_id=self.id, level="info", message=""))
        return results
