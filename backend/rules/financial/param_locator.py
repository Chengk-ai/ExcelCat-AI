"""
Parameter locator for financial review rules.

Strategy: label-value scan — keyword match on cell text, numeric value to the
right in the same row. Returns ALL matches (not just the first), each with its
value and location. If nothing is found, returns an empty list and the calling
rule silently skips.

Lookup is intentionally scoped to the selected region only — the review never
reaches outside the user's selection (this is why named-range lookup was
removed: a named range can point at a cell elsewhere in the workbook).
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class LocatedParam:
    value: float
    source: str
    cell: Optional[str] = None


# ── Keyword dictionaries ───────────────────────────────────────────────────────

WACC_KEYWORDS = frozenset([
    "wacc",
    "discount rate",
    "折现率",
    "weighted average cost of capital",
])

TGR_KEYWORDS = frozenset([
    "tgr",
    "terminal growth",
    "terminal growth rate",
    "永续增长",
    "永续增长率",
])

TAX_KEYWORDS = frozenset([
    "tax rate",
    "税率",
    "effective tax",
])

BETA_KEYWORDS = frozenset([
    "beta",
    "β",
    "贝塔",
    "levered beta",
    "unlevered beta",
])

DEBT_WEIGHT_KEYWORDS = frozenset([
    "% debt",
    "%debt",
    "debt weight",
    "weight of debt",
    "债务权重",
    "债务比例",
])

EQUITY_WEIGHT_KEYWORDS = frozenset([
    "% equity",
    "%equity",
    "equity weight",
    "weight of equity",
    "股权权重",
    "股权比例",
])

# Beta is naturally a small decimal (1.25), not a percentage — so it must NOT
# be divided by 100 when raw input looks > 1. Every other param the locator
# knows about is a percentage and benefits from the auto-scale.
IS_PERCENTAGE: Dict[str, bool] = {
    "wacc": True,
    "tgr": True,
    "tax": True,
    "beta": False,
    "debt_weight": True,
    "equity_weight": True,
}


# ── Address parsing helpers ────────────────────────────────────────────────────

_CELL_RE = re.compile(r"\$?([A-Z]+)\$?(\d+)")


def _col_to_index(col_letters: str) -> int:
    """Convert column letters to 0-based index: A→0, B→1, Z→25, AA→26."""
    result = 0
    for ch in col_letters:
        result = result * 26 + (ord(ch) - ord("A") + 1)
    return result - 1


def _index_to_col(idx: int) -> str:
    """Convert 0-based index to column letters: 0→A, 1→B, 25→Z, 26→AA."""
    letters = []
    idx += 1
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        letters.append(chr(rem + ord("A")))
    return "".join(reversed(letters))


def _parse_top_left(address: str) -> Optional[Tuple[int, int]]:
    """Extract the top-left cell from an address like 'Sheet!$B$5:$F$20'.

    Returns (col_index_0based, row_1based) or None if unparseable.
    """
    bare = address.split("!")[-1]
    m = _CELL_RE.match(bare)
    if not m:
        return None
    return _col_to_index(m.group(1)), int(m.group(2))


def _cell_ref(top_left: Tuple[int, int], row_idx: int, col_idx: int) -> str:
    col = _index_to_col(top_left[0] + col_idx)
    row = top_left[1] + row_idx
    return f"{col}{row}"


# ── Scale normalisation ───────────────────────────────────────────────────────

def _normalise_scale(value: float, is_percentage: bool) -> float:
    """If value > 1 AND this param is a percentage, convert to decimal.
    Non-percentage params (e.g. Beta) are passed through untouched."""
    if is_percentage and value > 1:
        return value / 100.0
    return value


# ── Label-value scan ───────────────────────────────────────────────────────────

def _matches_keywords(text: str, keywords: frozenset) -> bool:
    lower = text.strip().lower()
    return any(kw in lower for kw in keywords)


def _keyword_set_for(param: str) -> frozenset:
    return {
        "wacc": WACC_KEYWORDS,
        "tgr": TGR_KEYWORDS,
        "tax": TAX_KEYWORDS,
        "beta": BETA_KEYWORDS,
        "debt_weight": DEBT_WEIGHT_KEYWORDS,
        "equity_weight": EQUITY_WEIGHT_KEYWORDS,
    }.get(param, frozenset())


def _find_in_labels(
    param: str, values: List[List[Any]], address: str
) -> List[LocatedParam]:
    keywords = _keyword_set_for(param)
    if not keywords:
        return []

    is_pct = IS_PERCENTAGE.get(param, True)
    top_left = _parse_top_left(address)
    results: List[LocatedParam] = []

    for row_idx, row in enumerate(values):
        if not row:
            continue
        for col_idx, cell in enumerate(row):
            if not isinstance(cell, str):
                continue
            if not _matches_keywords(cell, keywords):
                continue
            for val_col, val in enumerate(row[col_idx + 1:], start=col_idx + 1):
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    cell_str = (
                        _cell_ref(top_left, row_idx, val_col)
                        if top_left else None
                    )
                    results.append(LocatedParam(
                        value=_normalise_scale(float(val), is_pct),
                        source="label match",
                        cell=cell_str,
                    ))
                    break

    return results


# ── Public API ─────────────────────────────────────────────────────────────────

def locate_all(
    param: str,
    values: List[List[Any]],
    address: str = "",
) -> List[LocatedParam]:
    """
    Locate ALL instances of a financial parameter via label-value scan.

    Args:
        param: one of "wacc", "tgr", "tax"
        values: 2D grid of the selected region
        address: selection address (e.g. "DCF!$B$5:$F$20") for cell-ref resolution

    Returns:
        List of LocatedParam, each with value in decimal scale (e.g. 0.10 for
        10%), source description, and cell reference when available. Empty list
        if nothing found.

    Lookup stays scoped to the selected region: only the supplied `values`
    grid is searched, never the wider workbook.
    """
    return _find_in_labels(param, values, address)
