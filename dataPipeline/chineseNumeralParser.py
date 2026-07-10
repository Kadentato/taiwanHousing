"""Parse Chinese-numeral floor counts (e.g. ``十二層`` -> 12) from the source.

Floor fields (``總樓層數`` total floors, ``移轉層次`` transferred floor) are written
as Chinese numerals with a ``層`` suffix. Values seen range from ``一層`` to the
high twenties; basements appear as ``地下N層`` and a few non-numeric markers
(``全``, ``見其他登記事項``) occur. Unparseable input returns ``None``.
"""

from __future__ import annotations

from typing import Optional

_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_UNITS = {"十": 10, "百": 100, "千": 1000}


def parseChineseNumber(value: Optional[str]) -> Optional[int]:
    """Convert a Chinese numeral (up to thousands) to an int, or ``None``."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():  # already Arabic
        return int(text)

    total = 0
    current = 0
    sawAny = False
    for ch in text:
        if ch in _DIGITS:
            current = _DIGITS[ch]
            sawAny = True
        elif ch in _UNITS:
            unit = _UNITS[ch]
            # A leading unit with no preceding digit means 1 (十二 -> 12, not 02).
            total += (current if current else 1) * unit
            current = 0
            sawAny = True
        else:
            # Unknown character -> not a clean numeral.
            return None
    if not sawAny:
        return None
    return total + current


def parseFloorCount(value: Optional[str]) -> Optional[int]:
    """Parse a floor-count field. Basements (``地下N層``) return negative numbers.

    Returns ``None`` for blanks and non-numeric markers such as ``全`` or
    ``見其他登記事項``.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    basement = False
    if text.startswith("地下"):
        basement = True
        text = text[2:]
    text = text.replace("地上", "")
    # Drop the trailing counter word(s).
    for suffix in ("層樓", "層", "樓"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break

    number = parseChineseNumber(text)
    if number is None:
        return None
    return -number if basement else number
