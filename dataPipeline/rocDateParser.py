"""Parse Republic-of-China (Minguo) dates from the LVR source data into ISO 8601.

The source uses several date encodings (all verified against the raw CSVs):

* ROC7 ``YYYMMDD``  e.g. ``1150601`` -> 2026-06-01, ``0680306`` -> 1979-03-06
* ROC5 ``YYYMM``    e.g. ``06701``  -> 1978-01-01 (year-month only; day defaults to 1)
* CJK  ``97年3月19日`` (build sub-table) -> 2008-03-19; ``97年3月`` -> 2008-03-01
* Range ``1150501~1160430`` (rental period) -> (start, end)

Anything unparseable returns ``None`` rather than raising, so a few malformed
rows never abort a build.

ROC year -> Gregorian year is ``rocYear + 1911``.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Optional, Tuple

ROC_OFFSET = 1911

_CJK_DATE = re.compile(r"(\d{1,3})\s*年\s*(\d{1,2})\s*月\s*(?:(\d{1,2})\s*日)?")


def _toIso(rocYear: int, month: int, day: int) -> Optional[str]:
    """Validate a (rocYear, month, day) triple and render it as an ISO date."""
    try:
        return date(rocYear + ROC_OFFSET, month, day).isoformat()
    except ValueError:
        return None


def parseRocDate(value: Optional[str]) -> Optional[str]:
    """Parse a single ROC date string into an ISO date, or ``None``."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    # CJK form: 97年3月19日 (day optional).
    cjk = _CJK_DATE.search(text)
    if cjk:
        year, month = int(cjk.group(1)), int(cjk.group(2))
        day = int(cjk.group(3)) if cjk.group(3) else 1
        return _toIso(year, month, day)

    # Pure-digit ROC forms.
    if text.isdigit():
        if len(text) == 7:  # YYYMMDD
            return _toIso(int(text[:3]), int(text[3:5]), int(text[5:7]))
        if len(text) == 6:  # YYMMDD (2-digit year, rare) -> treat as YY MM DD
            return _toIso(int(text[:2]), int(text[2:4]), int(text[4:6]))
        if len(text) == 5:  # YYYMM (year-month only)
            return _toIso(int(text[:3]), int(text[3:5]), 1)
    return None


def parseRocDateRange(value: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Parse a ``start~end`` ROC range (rental period) into (startIso, endIso)."""
    if value is None:
        return (None, None)
    text = str(value).strip()
    if "~" not in text:
        # Some rows may carry only a single date; treat it as the start.
        return (parseRocDate(text), None)
    start, _, end = text.partition("~")
    return (parseRocDate(start), parseRocDate(end))


def buildingAgeYears(completionIso: Optional[str], saleIso: Optional[str]) -> Optional[float]:
    """Whole-plus-fractional years between completion and sale, or ``None``.

    A building that completes after the sale (a new build sold pre-completion /
    off-plan, then registered as a sale) is treated as **age 0** rather than
    missing — that is the meaningful value and avoids inflating missingness.
    """
    if not completionIso or not saleIso:
        return None
    try:
        completion = date.fromisoformat(completionIso)
        sale = date.fromisoformat(saleIso)
    except ValueError:
        return None
    return round(max((sale - completion).days, 0) / 365.25, 1)
