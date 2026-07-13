"""Publish a compact SAMPLE database for the web "Browse database" page.

The full multi-year database is ~2–3 GB, which the in-browser sql.js viewer can't
load (it reads the whole file into memory). So we ship a representative random
sample of sale-housing rows plus the full small reference tables
(regions/cities/districts/tags). Run this once after a big
`buildDatabase.py --seasons-dir ...` build.

    python publishSampleDb.py [--rows 40000]
"""

from __future__ import annotations

import argparse
import os
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
FULL = os.path.join(HERE, "database", "taiwanHousing.sqlite")
OUT = os.path.join(HERE, "webApp", "dataFiles", "taiwanHousing.sqlite")
HOUSING = ("houseLand", "houseLandParking", "buildingOnly")
SMALL = ["regions", "cities", "districts", "tags"]


def main() -> int:
    ap = argparse.ArgumentParser(description="Publish a small sample DB for the browse page.")
    ap.add_argument("--rows", type=int, default=20000, help="approx. sample of sale-housing rows")
    args = ap.parse_args()

    if not os.path.exists(FULL):
        print(f"ERROR: {FULL} not found — run buildDatabase.py first.")
        return 1
    if os.path.exists(OUT):
        os.remove(OUT)

    dst = sqlite3.connect(OUT)
    dst.execute("ATTACH ? AS full", (FULL,))
    # recreate the shipped tables' schema, copy the small ones whole
    for t in SMALL + ["houses", "houseTags"]:
        ddl = dst.execute(
            "SELECT sql FROM full.sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()
        if ddl and ddl[0]:
            dst.execute(ddl[0])
    for t in SMALL:
        dst.execute(f"INSERT INTO {t} SELECT * FROM full.{t}")
    # ~1%-per-row random sample of sale housing, capped at --rows
    ph = ",".join("?" * len(HOUSING))
    dst.execute(f"INSERT INTO houses SELECT * FROM full.houses "
                f"WHERE targetType IN ({ph}) AND abs(random()) % 100 = 0 LIMIT ?",
                (*HOUSING, args.rows))
    dst.execute("INSERT INTO houseTags SELECT ht.* FROM full.houseTags ht "
                "WHERE ht.houseId IN (SELECT houseId FROM houses)")
    dst.commit()
    dst.execute("DETACH full")
    dst.execute("VACUUM")
    dst.commit()
    n = dst.execute("SELECT COUNT(*) FROM houses").fetchone()[0]
    dst.close()
    print(f"Sample published: {n:,} sale-housing rows -> {OUT} ({os.path.getsize(OUT) / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
