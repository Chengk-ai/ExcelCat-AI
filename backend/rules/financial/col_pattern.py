"""
Column-pattern abstraction for horizontal formula-consistency checks.

Transpose of `_row_template` in backend/main.py: replaces cell references
whose COLUMN matches the target with `{c}<row>`, keeping all other refs
literal. Numeric literals (100, 0.5) are untouched.

This is how we tell "=J42+J44-J47-J50" (col J) and "=K42+K44-K47-K50" (col K)
are the same year-by-year pattern, while spotting "=L42+L44-L47" as broken.
"""
from __future__ import annotations
import re


_CELL_REF_RE = re.compile(r'([A-Za-z]+)(\d+)')


def col_template(value: str, target_col: str) -> str:
    if not value:
        return ""
    target = target_col.upper()

    def sub(m: re.Match) -> str:
        col, num = m.group(1), m.group(2)
        if col.upper() == target:
            return f"{{c}}{num}"
        return f"{col.upper()}{num}"

    return _CELL_REF_RE.sub(sub, value)
