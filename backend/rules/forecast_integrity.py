"""
Forecast integrity checks — "declared vs actual" for apply_forecast.

The sanity band (forecast_sanity) trusts what the LLM *declares*: its
assumed_growth_rate and its echoed history_values. These checks close that gap
deterministically, against evidence already in hand:

  1. history provenance — the echoed history_values must actually appear in
     the selection grid. The model's ONLY data source is the context we gave
     it, so a history that isn't in the grid is miscopied or invented — and
     the sanity check would be anchored on fiction.
  2. rate reconciliation — a declared assumed_growth_rate must appear among
     the (1+x) literals written in the formulas. A stated assumption that
     disagrees with the written action is exactly what an audit product must
     catch.
  3. method guardrail — forecast.md forbids exponential methods on histories
     containing zero/negative values (GROWTH/CAGR error or mislead there).
     The contract states it; this rule enforces it.

Proof-of-work: when a check runs clean it emits an `info` result, so the
audit trail distinguishes "checked, fine" from "couldn't check" — the same
reasoning as review's inspected_cells counters.
"""
import math
import re
from typing import Any, List

from .base import Rule, RuleResult

# Rate literals inside chained growth formulas: (1+0.2), (1 + 0.30), (1+20%).
_RATE_RE = re.compile(r"\(\s*1\s*\+\s*([0-9]*\.?[0-9]+)\s*(%?)\s*\)")

# 0.5% relative tolerance: generous enough that a model echoing a lightly
# rounded figure still verifies, tight enough that the real threat — wrong
# row, wrong sheet region, invented numbers — cannot slip through.
_REL_TOL = 5e-3


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=_REL_TOL, abs_tol=1e-6)


def _numeric_seqs(grid) -> list:
    """Every row's and every column's numeric sequence, in order. Label cells
    and spacer columns drop out, so a 'contiguous history' still matches when
    the sheet interleaves blanks between periods."""
    seqs = []
    rows = grid or []
    for row in rows:
        seq = [float(v) for v in (row or [])
               if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if len(seq) >= 2:
            seqs.append(seq)
    n_cols = max((len(r) for r in rows), default=0)
    for c in range(n_cols):
        seq = []
        for row in rows:
            v = row[c] if c < len(row) else None
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                seq.append(float(v))
        if len(seq) >= 2:
            seqs.append(seq)
    return seqs


def _contains_run(seq: list, run: list) -> bool:
    n, m = len(seq), len(run)
    for i in range(n - m + 1):
        if all(_close(seq[i + j], run[j]) for j in range(m)):
            return True
    return False


class ForecastIntegrityRule(Rule):
    id = "forecast_integrity"
    level = "warning"

    def applies_to(self, tool_name: str) -> bool:
        return tool_name == "apply_forecast"

    def check(self, tool_call: dict, context: Any) -> List[RuleResult]:
        args = tool_call.get("args", {}) or {}
        results: List[RuleResult] = []

        history = [
            float(v) for v in (args.get("history_values") or [])
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        ]
        grid = getattr(context, "values", None) if context is not None else None

        # ── 1. History provenance ──
        # Needs ≥2 points (a single value would match almost anything) and a
        # selection grid to check against; otherwise recorded as unchecked.
        if len(history) >= 2 and grid:
            if any(_contains_run(seq, history) for seq in _numeric_seqs(grid)):
                results.append(RuleResult("forecast_history_verified", "info", ""))
            else:
                shown = ", ".join(f"{v:g}" for v in history[:8])
                results.append(RuleResult(
                    rule_id="forecast_history_mismatch",
                    level="warning",
                    message=(
                        f"The historical values this forecast claims to be based on "
                        f"({shown}) do not appear in the selected data — the projection "
                        f"may be anchored on miscopied or invented history. Please check "
                        f"the source series before approving."
                    ),
                ))
        else:
            results.append(RuleResult("forecast_history_unchecked", "info", ""))

        # ── 2. Declared rate vs written formulas ──
        # Only applicable when the formulas carry explicit (1+x) rate literals
        # (growth_rate chains). Curve fits (GROWTH) and CAGR expressions carry
        # no literal to reconcile, so they are simply out of scope here.
        declared = args.get("assumed_growth_rate")
        rates: List[float] = []
        for f in (args.get("values") or []):
            if isinstance(f, str):
                for num, pct in _RATE_RE.findall(f):
                    rates.append(float(num) / (100.0 if pct else 1.0))
        if isinstance(declared, (int, float)) and not isinstance(declared, bool) and rates:
            if any(_close(float(declared), r) for r in rates):
                results.append(RuleResult("forecast_rate_verified", "info", ""))
            else:
                found = ", ".join(f"{r * 100:g}%" for r in dict.fromkeys(rates))
                results.append(RuleResult(
                    rule_id="forecast_rate_mismatch",
                    level="warning",
                    message=(
                        f"The declared growth assumption ({float(declared) * 100:g}%) does "
                        f"not appear in the written formulas (rates found: {found}). The "
                        f"stated assumption and the actual formulas disagree — one of "
                        f"them is wrong."
                    ),
                ))

        # ── 3. Contract guardrail: exponential methods need positive history ──
        method = str(args.get("method", "")).lower()
        if "exponential" in method and history and min(history) <= 0:
            results.append(RuleResult(
                rule_id="forecast_method_guardrail",
                level="warning",
                message=(
                    "The history contains zero or negative values, but the method is "
                    "exponential — GROWTH/CAGR will error or mislead on such a series. "
                    "The forecast contract requires falling back to linear or an "
                    "explicit growth rate here."
                ),
            ))

        return results
