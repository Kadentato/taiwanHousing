"""Smoke test: load one city's sale file into an in-memory DB and sanity-check."""

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataPipeline.csvLoader import Loader  # noqa: E402
from dataPipeline.geoLookup import GeoLookup  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_DIR = r"C:\Users\Caden\Downloads\lvr_landcsv"
CITY = "i"  # Chiayi City (small file)
CENTROIDS = os.path.join(ROOT, "geoReference", "districtCentroids.csv")


def _sourceRowCount(path):
    with open(path, encoding="utf-8-sig") as fh:
        return sum(1 for _ in fh) - 2  # minus the two header rows


@pytest.mark.skipif(
    not (os.path.exists(SOURCE_DIR) and os.path.exists(CENTROIDS)),
    reason="needs source CSVs + geoReference",
)
def testLoadChiayiSale():
    conn = sqlite3.connect(":memory:")
    loader = Loader(conn, SOURCE_DIR, GeoLookup(CENTROIDS))
    loader.createSchema()
    loader.seedHierarchy()
    stats = loader.loadFile(CITY, "a")

    expected = _sourceRowCount(os.path.join(SOURCE_DIR, f"{CITY}_lvr_land_a.csv"))
    assert stats["sale"] == expected

    n = conn.execute("SELECT COUNT(*) FROM houses").fetchone()[0]
    assert n == expected

    # Every house resolved to a city + district and a parsed sale date.
    assert conn.execute("SELECT COUNT(*) FROM houses WHERE cityId IS NULL").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM houses WHERE districtId IS NULL").fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM houses WHERE saleDate IS NULL").fetchone()[0] == 0

    # Translations produced English enum values, not raw Mandarin.
    types = {r[0] for r in conn.execute(
        "SELECT DISTINCT transactionType FROM houses").fetchall()}
    assert types == {"sale"}
    bt = conn.execute(
        "SELECT buildingType FROM houses WHERE buildingType IS NOT NULL LIMIT 1").fetchone()
    assert bt and bt[0].isascii()

    # Ping conversion is consistent.
    row = conn.execute(
        "SELECT buildingAreaM2, buildingAreaPing FROM houses "
        "WHERE buildingAreaM2 > 0 AND buildingAreaPing > 0 LIMIT 1").fetchone()
    assert abs(row[0] / row[1] - 3.305785) < 0.01
    conn.close()
