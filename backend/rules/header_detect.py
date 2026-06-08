"""
Header detection for Type 2 rules.

Two responsibilities:
1. Heuristic: decide whether the first row of the selection is a header row.
2. Dictionary: map header text to a semantic type (date, email, percentage).

Design: high precision, low recall. If unsure → return None → Type 2 skips.
"""
from typing import Any, Dict, List, Optional

# ── Keyword dictionary ──────────────────────────────────────────────
# Maps lowercase keywords to semantic types. A header matches if it
# contains any keyword as a substring (case-insensitive).
HEADER_KEYWORDS: Dict[str, str] = {
    "date": "date",
    "日期": "date",
    "time": "date",
    "timestamp": "date",
    "created": "date",
    "updated": "date",
    "deadline": "date",
    "due": "date",

    "email": "email",
    "e-mail": "email",
    "邮箱": "email",

    "percent": "percentage",
    "percentage": "percentage",
    "pct": "percentage",
    "%": "percentage",
    "rate": "percentage",
    "ratio": "percentage",
    "margin": "percentage",
    "yield": "percentage",
}


def detect_header_row(values: List[List[Any]]) -> bool:
    """
    Heuristic: the first row is a header if:
    - It has at least one cell
    - ALL cells in row 0 are non-empty strings
    - AND at least one of:
      (a) at least one cell in rows 1+ is numeric, OR
      (b) at least one cell in row 0 matches the keyword dictionary

    Returns False if the selection has <2 rows or the heuristic fails.
    """
    if not values or len(values) < 2:
        return False

    first_row = values[0]
    if not first_row:
        return False

    for cell in first_row:
        if not isinstance(cell, str) or cell.strip() == "":
            return False

    for row in values[1:]:
        for cell in row:
            if isinstance(cell, (int, float)):
                return True

    for cell in first_row:
        if classify_header(str(cell)) is not None:
            return True

    return False


def classify_header(header_text: str) -> Optional[str]:
    """
    Look up a header string in the keyword dictionary.
    Returns the semantic type ('date', 'email', 'percentage') or None.
    """
    if not header_text:
        return None
    lower = header_text.strip().lower()
    for keyword, sem_type in HEADER_KEYWORDS.items():
        if keyword in lower:
            return sem_type
    return None


def get_column_type(col_index: int, values: List[List[Any]]) -> Optional[str]:
    """
    Given a 0-based column index within the selection, detect the header
    row and classify the column's header.

    Returns the semantic type or None (meaning Type 2 rules should skip).
    """
    if not detect_header_row(values):
        return None
    first_row = values[0]
    if col_index < 0 or col_index >= len(first_row):
        return None
    return classify_header(str(first_row[col_index]))
