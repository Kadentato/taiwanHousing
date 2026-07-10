"""Romanise Mandarin district names to English.

The LVR source only gives district names in Chinese, so we generate English
names for the map/UI. Most Taiwan districts use Hanyu Pinyin officially (since
2009), which ``pypinyin`` produces; a small override table covers the well-known
exceptions (Tamsui, Lukang, the directional districts, city names with
traditional spellings). Output looks like "Xinyi District", "Toufen City".
"""

from __future__ import annotations

from typing import Optional

from pypinyin import lazy_pinyin

# Administrative-unit suffix -> English type word.
TYPE_WORD = {"區": "District", "市": "City", "鎮": "Township", "鄉": "Township"}
# Single-character directional cores translate rather than transliterate.
DIRECTIONAL = {"東": "East", "西": "West", "南": "South", "北": "North", "中": "Central"}
# Cores whose established English differs from plain Hanyu Pinyin.
CORE_OVERRIDE = {
    "淡水": "Tamsui", "鹿港": "Lukang", "中西": "West Central",
    "嘉義": "Chiayi", "新竹": "Hsinchu", "基隆": "Keelung",
}


def _joinPinyin(syllables) -> str:
    """Join syllables, inserting the pinyin apostrophe before a/e/o (Da'an, Ren'ai)."""
    out = syllables[0]
    for s in syllables[1:]:
        out += ("'" + s) if s[:1] in "aeo" else s
    return out.capitalize()


def toEnglish(nameZh: Optional[str]) -> Optional[str]:
    name = ("" if nameZh is None else str(nameZh)).strip()
    if not name:
        return None
    core, typeWord = name, ""
    for suffix, word in TYPE_WORD.items():
        if len(core) > 1 and core.endswith(suffix):
            core, typeWord = core[:-1], word
            break
    if core in CORE_OVERRIDE:
        base = CORE_OVERRIDE[core]
    elif core in DIRECTIONAL:
        base = DIRECTIONAL[core]
    else:
        base = _joinPinyin(lazy_pinyin(core))
    return (base + (" " + typeWord if typeWord else "")).strip()
