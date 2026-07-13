"""
Clean integrity checks — "declared vs actual" for apply_cleaning.

apply_cleaning writes LLM-generated literals over existing user data — on its
face the least auditable thing the product could do. These checks make it the
most auditable action instead: every fix must be provably mechanical.

  1. old-value provenance — each old_values[i] must equal the sheet's actual
     content at cells[i], character for character. The approval card's
     "before" column must never lie, and the model may only touch cells that
     exist inside the selection it was shown.
  2. transform correctness — new_values[i] must equal fix_types[i] applied to
     old_values[i], recomputed here in Python. The LLM claims "trim"; Python
     proves the change was ONLY a trim.

Misaligned arrays and unknown fix types are warnings too. Proof-of-work: a
fully verified proposal emits clean_transforms_verified / clean_provenance_
verified info markers, so the audit trail shows the proposal was PROVEN
mechanical, not merely unflagged. No LLM anywhere in this rule.
"""
import re
from typing import Any, List, Optional, Tuple

from .base import Rule, RuleResult

# Matches Excel TRIM semantics: strip both ends AND collapse internal runs.
_TRANSFORMS = {
    "trim": lambda s: " ".join(s.split()),
    "case_title": lambda s: s.title(),
    "case_upper": lambda s: s.upper(),
    "case_lower": lambda s: s.lower(),
}

# Editorial title case: .title() with a FIXED minor-word list lowered when not
# the first or last word — "Cost of Sales", not "Cost Of Sales". The list is
# spelled out in the contract, so this stays a lookup, not a judgement: the
# transform remains 100% verifiable.
_MINOR_WORDS = frozenset((
    "a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or",
    "the", "to", "with",
))


def _editorial_title(s: str) -> str:
    t = s.title()
    # Tokenise into words and whitespace runs so original spacing survives.
    tokens = re.findall(r"\S+|\s+", t)
    word_idx = [i for i, tok in enumerate(tokens) if not tok.isspace()]
    if not word_idx:
        return t
    first, last = word_idx[0], word_idx[-1]
    for i in word_idx:
        if i not in (first, last) and tokens[i].lower() in _MINOR_WORDS:
            tokens[i] = tokens[i].lower()
    return "".join(tokens)


def _acceptable_outputs(fix: str, old: str):
    """The set of results the declared fix may legitimately produce, or None
    for an unknown fix type. case_title accepts BOTH precisely-defined forms:
    Excel PROPER ("Cost Of Sales") and editorial ("Cost of Sales")."""
    fn = _TRANSFORMS.get(fix)
    if fn is None:
        return None
    outs = {fn(old)}
    if fix == "case_title":
        outs.add(_editorial_title(old))
    return outs

_CELL_RE = re.compile(r"^\$?([A-Za-z]+)\$?(\d+)$")


def _col_index(letters: str) -> int:
    col = 0
    for ch in letters.upper():
        col = col * 26 + (ord(ch) - ord("A") + 1)
    return col - 1


def _parse_top_left(address: str) -> Optional[Tuple[int, int]]:
    """Top-left of 'Sheet1!B2:F20' → (col_idx_0based, row_1based). Third copy
    of this tiny parser (param_locator, profile_stats) — worth promoting to a
    shared helper if it grows a fourth caller."""
    bare = (address or "").split("!")[-1].split(":")[0]
    m = _CELL_RE.match(bare.strip())
    if not m:
        return None
    return _col_index(m.group(1)), int(m.group(2))


_MISSING = object()   # can't resolve at all (no context / unparseable)
_OUTSIDE = object()   # resolvable, but the cell is outside the selection


def _grid_value(context: Any, cell: str):
    values = getattr(context, "values", None) if context is not None else None
    if not values:
        return _MISSING
    origin = _parse_top_left(getattr(context, "address", "") or "")
    if origin is None:
        return _MISSING
    m = _CELL_RE.match((cell or "").strip())
    if not m:
        return _MISSING
    c_idx = _col_index(m.group(1)) - origin[0]
    r_idx = int(m.group(2)) - origin[1]
    if r_idx < 0 or r_idx >= len(values):
        return _OUTSIDE
    row = values[r_idx] or []
    if c_idx < 0 or c_idx >= len(row):
        return _OUTSIDE
    return row[c_idx]


class CleanIntegrityRule(Rule):
    id = "clean_integrity"
    level = "warning"

    def applies_to(self, tool_name: str) -> bool:
        return tool_name == "apply_cleaning"

    def check(self, tool_call: dict, context: Any) -> List[RuleResult]:
        args = tool_call.get("args", {}) or {}
        results: List[RuleResult] = []

        cells = args.get("cells") or []
        olds = args.get("old_values") or []
        news = args.get("new_values") or []
        fixes = args.get("fix_types") or []

        if not cells:
            return [RuleResult(
                "clean_empty", "warning",
                "The cleaning proposal contains no cells — nothing to apply.",
            )]
        if not (len(cells) == len(olds) == len(news) == len(fixes)):
            return [RuleResult(
                "clean_arrays_misaligned", "warning",
                f"Misaligned proposal ({len(cells)} cells, {len(olds)} old values, "
                f"{len(news)} new values, {len(fixes)} fix types) — the fixes "
                f"cannot be verified; do not approve.",
            )]

        unknown: List[str] = []
        bad_transform: List[str] = []
        bad_old: List[str] = []
        outside: List[str] = []
        provenance_possible = True

        for i, cell in enumerate(cells):
            cell = str(cell)
            fix = str(fixes[i])
            old = olds[i] if isinstance(olds[i], str) else str(olds[i])
            new = news[i] if isinstance(news[i], str) else str(news[i])

            outs = _acceptable_outputs(fix, old)
            if outs is None:
                unknown.append(f"{cell} ({fix})")
            elif new not in outs:
                bad_transform.append(cell)

            gv = _grid_value(context, cell)
            if gv is _MISSING:
                provenance_possible = False
            elif gv is _OUTSIDE:
                outside.append(cell)
            elif not (isinstance(gv, str) and gv == old):
                # Text fixes only: a non-string grid value can never honestly
                # be the "old value" of a trim/case fix.
                bad_old.append(cell)

        def _lst(items: List[str]) -> str:
            shown = ", ".join(items[:5])
            return shown + (f" (and {len(items) - 5} more)" if len(items) > 5 else "")

        if unknown:
            results.append(RuleResult(
                "clean_unknown_fix_type", "warning",
                f"Unknown fix type on {_lst(unknown)} — only trim / case_title / "
                f"case_upper / case_lower can be verified. Do not approve "
                f"unverifiable fixes.",
            ))
        if bad_transform:
            results.append(RuleResult(
                "clean_transform_mismatch", "warning",
                f"The new value is NOT exactly the declared fix applied to the old "
                f"value at {_lst(bad_transform)} — the change is more than the "
                f"mechanical fix it claims to be. Review those cells before approving.",
            ))
        if outside:
            results.append(RuleResult(
                "clean_cell_outside_selection", "warning",
                f"Cell(s) {_lst(outside)} fall outside the selected range — fixes "
                f"may only target cells the model was actually shown.",
            ))
        if bad_old:
            results.append(RuleResult(
                "clean_old_value_mismatch", "warning",
                f"The 'before' value shown for {_lst(bad_old)} does not match what "
                f"is actually in the sheet — approving would act on false "
                f"information.",
            ))

        # Proof-of-work markers: PROVEN mechanical, not merely unflagged.
        if not unknown and not bad_transform:
            results.append(RuleResult("clean_transforms_verified", "info", ""))
        if provenance_possible and not bad_old and not outside:
            results.append(RuleResult("clean_provenance_verified", "info", ""))
        elif not provenance_possible:
            results.append(RuleResult("clean_provenance_unchecked", "info", ""))

        return results
