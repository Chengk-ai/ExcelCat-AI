"""
DCF integrity checks — "declared vs actual" for apply_dcf_template.

DcfSanityRule trusts what the LLM *declares* (wacc, drivers, historical
series). This rule closes the gap deterministically against the emitted
sheets themselves — the forecast_integrity philosophy applied to the DCF
template:

  1. structure — exactly a WACC sheet and a DCF sheet, cells/values aligned.
  2. assumption presence — the declared WACC/TGR cells hold numbers matching
     the declared assumptions; the declared driver rows hold the declared
     per-year driver values.
  3. formula wiring — discount formulas (PV rows) reference the DCF sheet's
     WACC cell (B3, which itself must reference WACC!B16); the terminal-value
     formula references both the WACC and TGR cells; none of them buries a
     hardcoded rate in a (1+x) position. This is what makes "edit an
     assumption and the valuation recalculates" true rather than aspirational.
  4. historical provenance echo — the DCF sheet's historical Revenue/EBIT rows
     hold the declared historical series (which the orchestrator has already
     verified against the SOURCE grids), so declaration, sheet, and source
     agree end-to-end.

Never blocks — findings ride the approval card as warnings; proof-of-work
info results mark checks that ran clean.
"""
import math
import re
from typing import Any, Dict, List, Optional

from ..base import Rule, RuleResult

_REL_TOL = 5e-3

# The template's pinned anchors (backend/skills/dcf.md).
WACC_SHEET, DCF_SHEET = "WACC", "DCF"
WACC_CELL = "B16"          # on the WACC sheet
DCF_WACC_REF = "B3"        # on the DCF sheet, must be =WACC!B16
DCF_TGR_CELL = "B4"
PV_ROW, TV_ROW = 22, 24

# A literal rate inside (1+x) — allowed only when x is a cell ref, not a number.
_HARDCODED_RATE_RE = re.compile(r"\(\s*1\s*[+\-]\s*[0-9]*\.?[0-9]+\s*%?\s*\)")

# "discounts with the WACC cell": 1+B3 with optional parentheses around the
# ref — =X/(1+(B3))^n is structurally correct too; substring matching alone
# would false-flag it. Matched against the $-stripped, space-stripped formula.
_PV_WACC_RE = re.compile(r"1\+\(*" + DCF_WACC_REF + r"\)*(?![0-9])")


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=_REL_TOL, abs_tol=1e-6)


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def _cell_map(sheet: dict) -> Optional[Dict[str, str]]:
    """address (normalised) → value for one declared sheet, or None if misaligned."""
    cells = sheet.get("cells") or []
    values = sheet.get("values") or []
    if len(cells) != len(values):
        return None
    return {str(c).replace("$", "").strip().upper(): v for c, v in zip(cells, values)}


def _row_cells(cmap: Dict[str, str], row: int) -> Dict[str, str]:
    """column letter → value for every populated cell of one row."""
    out = {}
    for addr, v in cmap.items():
        m = re.match(r"([A-Z]+)(\d+)$", addr)
        if m and int(m.group(2)) == row:
            out[m.group(1)] = v
    return out


def _sorted_row_values(cmap: Dict[str, str], row: int) -> List[str]:
    def col_key(letters: str) -> int:
        n = 0
        for ch in letters:
            n = n * 26 + (ord(ch) - 64)
        return n
    cells = _row_cells(cmap, row)
    return [cells[c] for c in sorted(cells, key=col_key)]


class DcfIntegrityRule(Rule):
    id = "dcf_integrity"
    level = "warning"

    def applies_to(self, tool_name: str) -> bool:
        return tool_name == "apply_dcf_template"

    def check(self, tool_call: dict, context: Any) -> List[RuleResult]:
        args = tool_call.get("args", {}) or {}
        results: List[RuleResult] = []

        # ── 1. Structure ──
        sheets = {str(s.get("name", "")).strip().upper(): s
                  for s in (args.get("sheets") or []) if isinstance(s, dict)}
        if set(sheets) != {WACC_SHEET, DCF_SHEET}:
            results.append(RuleResult(
                rule_id="dcf_structure", level="warning",
                message=(
                    f"Expected exactly two sheets named {WACC_SHEET} and {DCF_SHEET}; "
                    f"got: {sorted(sheets) or 'none'}. The template layout was not followed."
                ),
            ))
            return results
        wacc_map = _cell_map(sheets[WACC_SHEET])
        dcf_map = _cell_map(sheets[DCF_SHEET])
        if wacc_map is None or dcf_map is None:
            results.append(RuleResult(
                rule_id="dcf_structure", level="warning",
                message="cells/values arrays are misaligned — the write cannot be verified.",
            ))
            return results

        # ── 2. Assumption presence ──
        wacc_val = _num(args.get("wacc"))
        sheet_wacc_formula = str(wacc_map.get(WACC_CELL, ""))
        if not sheet_wacc_formula.startswith("="):
            results.append(RuleResult(
                rule_id="dcf_wacc_cell", level="warning",
                message=(
                    f"{WACC_SHEET}!{WACC_CELL} should hold the WACC formula built from "
                    f"the CAPM components; found {sheet_wacc_formula!r}."
                ),
            ))
        dcf_wacc_ref = str(dcf_map.get(DCF_WACC_REF, "")).replace(" ", "").upper()
        if f"{WACC_SHEET}!" not in dcf_wacc_ref or WACC_CELL not in dcf_wacc_ref:
            results.append(RuleResult(
                rule_id="dcf_wacc_link", level="warning",
                message=(
                    f"DCF!{DCF_WACC_REF} should reference {WACC_SHEET}!{WACC_CELL} "
                    f"so the DCF recalculates when the CAPM inputs change; found "
                    f"{dcf_map.get(DCF_WACC_REF)!r}."
                ),
            ))
        tgr = _num(args.get("terminal_growth"))
        sheet_tgr = _num(dcf_map.get(DCF_TGR_CELL))
        if tgr is not None:
            if sheet_tgr is None or not _close(sheet_tgr, tgr):
                results.append(RuleResult(
                    rule_id="dcf_tgr_cell", level="warning",
                    message=(
                        f"Declared terminal growth {tgr} does not match "
                        f"DCF!{DCF_TGR_CELL} ({dcf_map.get(DCF_TGR_CELL)!r})."
                    ),
                ))

        # ── 2b. Driver rows hold the declared per-year values ──
        drivers = args.get("drivers") or {}
        driver_rows = (args.get("assumption_cells") or {}).get("driver_rows") or {}
        n_forecast = None
        try:
            n_forecast = int(args.get("forecast_years"))
        except (TypeError, ValueError):
            pass
        for name, row in driver_rows.items():
            declared = drivers.get(name)
            try:
                row = int(row)
            except (TypeError, ValueError):
                continue
            if not isinstance(declared, list) or not declared:
                continue
            row_vals = [_num(v) for v in _sorted_row_values(dcf_map, row)]
            row_nums = [v for v in row_vals if v is not None]
            # The declared values must appear as the row's trailing numbers
            # (historical ratio cells in the same row are formulas, so they
            # don't parse as numbers here).
            tail = row_nums[-len(declared):] if len(row_nums) >= len(declared) else row_nums
            if len(tail) != len(declared) or not all(
                _close(a, b) for a, b in zip(tail, [_num(d) for d in declared])
                if b is not None
            ):
                results.append(RuleResult(
                    rule_id="dcf_driver_mismatch", level="warning",
                    message=(
                        f"Driver '{name}': the declared per-year values {declared} do "
                        f"not match the numbers on DCF row {row}. The card would show "
                        f"assumptions the sheet does not use."
                    ),
                ))
            if n_forecast and len(declared) != n_forecast:
                results.append(RuleResult(
                    rule_id="dcf_driver_length", level="warning",
                    message=(
                        f"Driver '{name}' declares {len(declared)} values for "
                        f"{n_forecast} forecast years."
                    ),
                ))

        # ── 3. Formula wiring: PV + TV reference the assumption cells ──
        pv_formulas = [str(v) for v in _sorted_row_values(dcf_map, PV_ROW)
                       if str(v).startswith("=")]
        for f in pv_formulas:
            fu = f.replace("$", "").replace(" ", "").upper()
            if not _PV_WACC_RE.search(fu):
                results.append(RuleResult(
                    rule_id="dcf_pv_wiring", level="warning",
                    message=(
                        f"A PV formula ({f}) does not discount with the WACC cell "
                        f"(expected (1+${DCF_WACC_REF[0]}${DCF_WACC_REF[1:]})^n). "
                        f"Editing WACC would not recalculate it."
                    ),
                ))
                break
        tv_formulas = [str(v) for v in _sorted_row_values(dcf_map, TV_ROW)
                       if str(v).startswith("=")]
        if tv_formulas:
            fu = tv_formulas[-1].replace("$", "").replace(" ", "").upper()
            missing = [c for c in (DCF_WACC_REF, DCF_TGR_CELL) if c not in fu]
            if missing:
                results.append(RuleResult(
                    rule_id="dcf_tv_wiring", level="warning",
                    message=(
                        f"The terminal-value formula ({tv_formulas[-1]}) does not "
                        f"reference {' and '.join('DCF!' + c for c in missing)} — "
                        f"editing those assumptions would not recalculate it."
                    ),
                ))
        for f in pv_formulas + tv_formulas:
            if _HARDCODED_RATE_RE.search(f):
                results.append(RuleResult(
                    rule_id="dcf_hardcoded_rate", level="warning",
                    message=(
                        f"Formula {f} buries a literal rate in a (1+x) position "
                        f"instead of referencing an assumption cell."
                    ),
                ))
                break

        # ── 4. Historical echo: sheet rows hold the declared series ──
        hist = args.get("historical") or {}
        for name, row in (("revenue", 7), ("ebit", 9)):
            declared = [_num(v) for v in (hist.get(name) or [])]
            if not declared:
                continue
            row_nums = [v for v in (_num(x) for x in _sorted_row_values(dcf_map, row))
                        if v is not None]
            head = row_nums[:len(declared)]
            if len(head) != len(declared) or not all(
                _close(a, b) for a, b in zip(head, declared) if b is not None
            ):
                results.append(RuleResult(
                    rule_id="dcf_historical_mismatch", level="warning",
                    message=(
                        f"Historical {name} on the DCF sheet (row {row}) does not "
                        f"match the declared series — the sheet would show history "
                        f"different from the audited source figures."
                    ),
                ))

        if not results:
            results.append(RuleResult(rule_id=self.id, level="info", message=""))
        return results
