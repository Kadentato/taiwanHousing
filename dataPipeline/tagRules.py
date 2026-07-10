"""Extensible auto-tagging. Each rule maps a house record to zero or more tag
slugs; ``applyTags`` seeds the catalog and fills ``houseTags``.

This pass ships the **parking & management** categories. Add a new category by
appending entries to ``TAG_CATALOG`` and a function to ``RULES`` — nothing else
in the pipeline changes.
"""

from __future__ import annotations

import sqlite3
from typing import List

# slug -> (English label, Mandarin label, category)
TAG_CATALOG = {
    "hasParking":        ("Has parking", "有車位", "parking"),
    "noParking":         ("No parking", "無車位", "parking"),
    "parkingType:rampPlane":       ("Parking: ramp-level", "坡道平面", "parking"),
    "parkingType:rampMechanical":  ("Parking: ramp-mechanical", "坡道機械", "parking"),
    "parkingType:firstFloorPlane": ("Parking: ground-level", "一樓平面", "parking"),
    "parkingType:liftMechanical":  ("Parking: lift-mechanical", "升降機械", "parking"),
    "parkingType:liftPlane":       ("Parking: lift-level", "升降平面", "parking"),
    "parkingType:tower":           ("Parking: tower", "塔式車位", "parking"),
    "parkingType:other":           ("Parking: other", "其他車位", "parking"),
    "hasManagementOrg":  ("Has management org", "有管理組織", "management"),
    "noManagementOrg":   ("No management org", "無管理組織", "management"),
    "hasElevator":       ("Has elevator", "有電梯", "management"),
    "noElevator":        ("No elevator", "無電梯", "management"),
}


def _parkingRule(house: sqlite3.Row) -> List[str]:
    slugs: List[str] = []
    if house["hasParking"]:
        slugs.append("hasParking")
        if house["parkingType"]:
            slugs.append(f"parkingType:{house['parkingType']}")
    else:
        slugs.append("noParking")
    return slugs


def _managementRule(house: sqlite3.Row) -> List[str]:
    slugs: List[str] = []
    org = house["hasManagementOrg"]
    if org is not None:
        slugs.append("hasManagementOrg" if org else "noManagementOrg")
    lift = house["hasElevator"]
    if lift is not None:
        slugs.append("hasElevator" if lift else "noElevator")
    return slugs


RULES = [_parkingRule, _managementRule]


def applyTags(conn: sqlite3.Connection) -> dict:
    """Seed the tag catalog and populate houseTags. Returns slug -> count."""
    cur = conn.cursor()
    tagId = {}
    for slug, (en, zh, category) in TAG_CATALOG.items():
        cur.execute(
            "INSERT INTO tags(slug, labelEn, labelZh, category) VALUES (?,?,?,?)",
            (slug, en, zh, category),
        )
        tagId[slug] = cur.lastrowid

    conn.row_factory = sqlite3.Row
    counts = {slug: 0 for slug in TAG_CATALOG}
    pairs = []
    for house in conn.execute(
        "SELECT houseId, hasParking, parkingType, hasManagementOrg, hasElevator FROM houses"
    ):
        for rule in RULES:
            for slug in rule(house):
                pairs.append((house["houseId"], tagId[slug]))
                counts[slug] += 1
    conn.executemany("INSERT OR IGNORE INTO houseTags(houseId, tagId) VALUES (?,?)", pairs)
    conn.commit()
    return counts
