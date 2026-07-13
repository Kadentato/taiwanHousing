"""Write every district's full sale-housing records as compact gzipped files for the web map's
lazy per-district loading. Run after a big buildDatabase build; then run geocodeDoorplate.py to
overwrite the geocoded cities' files with real coordinates.

    python exportDistrictRecords.py
"""
from __future__ import annotations

import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from dataPipeline import districtRecords as dr  # noqa: E402

DB = os.path.join(HERE, "database", "taiwanHousing.sqlite")
OUT = os.path.join(HERE, "webApp", "dataFiles")


def main() -> int:
    if not os.path.exists(DB):
        print(f"ERROR: {DB} not found — run buildDatabase.py first.")
        return 1
    conn = sqlite3.connect(DB)
    nD, nR, nB = dr.exportAll(conn, OUT)
    conn.close()
    print(f"Wrote {nD} district files, {nR:,} sales, {nB / 1e6:.1f} MB gzipped "
          f"-> {os.path.join(OUT, 'districtRecords')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
