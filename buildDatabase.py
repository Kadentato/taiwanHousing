"""Build the Taiwan Housing database and web data from the LVR CSVs.

Pipeline:  create schema -> seed hierarchy -> load CSVs -> auto-tag ->
build spatial geometry -> export static web data.

Usage:
    python buildDatabase.py [--source-dir DIR] [--db PATH] [--web-dir DIR]

Requires ``geoReference/`` to exist (run ``setupGeoReference.py`` once first).
Everything here is fully offline.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from dataPipeline import tagRules, webDataExporter  # noqa: E402
from dataPipeline.csvLoader import Loader  # noqa: E402
from dataPipeline.geoLookup import GeoLookup  # noqa: E402
from dataPipeline.spatialBuilder import buildSpatial  # noqa: E402

DEFAULT_SOURCE_DIR = r"C:\Users\Caden\Downloads\lvr_landcsv"
DEFAULT_DB = os.path.join(HERE, "database", "taiwanHousing.sqlite")
DEFAULT_WEB_DIR = os.path.join(HERE, "webApp", "dataFiles")
GEO_CENTROIDS = os.path.join(HERE, "geoReference", "districtCentroids.csv")
MAX_PUBLISH_MB = 80   # don't ship a DB bigger than this to the browser (download/sql.js)


def _seasonFolders(seasonsDir: str):
    """(name, path) for each valid season subfolder, sorted NEWEST first."""
    out = []
    for name in sorted(os.listdir(seasonsDir)):
        path = os.path.join(seasonsDir, name)
        if os.path.isdir(path) and os.path.exists(os.path.join(path, "manifest.csv")):
            out.append((name, path))

    def key(item):
        try:
            roc, q = item[0].upper().split("S")
            return (int(roc), int(q))
        except Exception:
            return (0, 0)

    out.sort(key=key, reverse=True)   # newest first: newest disclosure wins dedup
    return out


def _readDisclosure(sourceDir: str):
    """Pull the LVR disclosure-window text (the real sampling frame) from build_time.xml."""
    path = os.path.join(sourceDir, "build_time.xml")
    if not os.path.exists(path):
        return None
    try:
        import re
        text = open(path, encoding="utf-8").read()
        m = re.search(r"<lvr_time>(.*?)</lvr_time>", text, re.S)
        return m.group(1).strip() if m else None
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Taiwan Housing database.")
    parser.add_argument("--source-dir", default=DEFAULT_SOURCE_DIR,
                        help="a single LVR release folder")
    parser.add_argument("--seasons-dir", default=None,
                        help="a folder of per-season subfolders (from fetchHistory.py); "
                             "loads all, newest-first, de-duplicated on serial number")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--web-dir", default=DEFAULT_WEB_DIR)
    parser.add_argument("--no-subtables", action="store_true",
                        help="skip build/land/park sub-tables (the web export doesn't use them) "
                             "— much faster/smaller for a full-history build")
    parser.add_argument("--sales-only", action="store_true",
                        help="load only sale (_a) files, not pre-sale/rental")
    args = parser.parse_args()

    if not os.path.exists(GEO_CENTROIDS):
        print("ERROR: geoReference/districtCentroids.csv missing. "
              "Run `python setupGeoReference.py` once first.", file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(args.db), exist_ok=True)
    if os.path.exists(args.db):
        os.remove(args.db)

    started = time.time()
    geo = GeoLookup(GEO_CENTROIDS)
    conn = sqlite3.connect(args.db)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=OFF")  # safe: rebuildable artifact

        print("1/5  Creating schema + seeding hierarchy ...")
        loader = Loader(conn, args.source_dir, geo)
        loader.loadSubTables = not args.no_subtables
        loader.salesOnly = args.sales_only
        loader.createSchema()
        loader.seedHierarchy()

        if args.seasons_dir:
            seasons = _seasonFolders(args.seasons_dir)
            print(f"2/5  Loading {len(seasons)} seasons (newest-first, de-duplicated) ...")
            stats = {"sale": 0, "presale": 0, "rental": 0, "build": 0, "land": 0, "park": 0}
            for i, (name, path) in enumerate(seasons, 1):
                s = loader.loadSeasonDir(path, season=name)
                for k, v in s.items():
                    stats[k] += v
                print(f"       [{i}/{len(seasons)}] {name}: +{s['sale'] + s['presale'] + s['rental']} unique"
                      f"  (running total {stats['sale'] + stats['presale'] + stats['rental']:,})")
        else:
            print("2/5  Loading CSVs ...")
            stats = loader.loadAll()
        print(f"       houses: sale={stats['sale']} presale={stats['presale']} rental={stats['rental']}"
              f"  | sub-rows: build={stats['build']} land={stats['land']} park={stats['park']}")

        print("3/5  Applying tags ...")
        tagCounts = tagRules.applyTags(conn)
        print(f"       {sum(v for v in tagCounts.values())} tag links across {len(tagCounts)} tags")

        print("4/5  Building spatial geometry (geopandas) ...")
        geoStats = buildSpatial(conn)
        print(f"       city polygons={geoStats['cities']} region polygons={geoStats['regions']}")

        print("5/5  Exporting web data ...")
        if args.seasons_dir:
            disclosure = (f"Historical bulk releases {seasons[-1][0]}–{seasons[0][0]} "
                          "(quarterly, de-duplicated on serial number).")
        else:
            disclosure = _readDisclosure(args.source_dir)
        exp = webDataExporter.exportAll(conn, args.web_dir, disclosure=disclosure)
        print(f"       geojson: districts={exp['districts']} cities={exp['cities']} regions={exp['regions']}"
              f"  | record files={exp['recordFiles']}")

        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")  # plain journal: opens cleanly in sql.js
    finally:
        conn.close()

    # Publish a copy of the finished database for the web app's download / in-browser
    # browse — but only if it's small enough to serve. A full multi-year history can be
    # ~1 GB, which isn't downloadable/browsable in a browser; then it stays local-only.
    dbCopy = os.path.join(args.web_dir, os.path.basename(args.db))
    sizeMb = os.path.getsize(args.db) / 1e6
    if sizeMb <= MAX_PUBLISH_MB:
        shutil.copy2(args.db, dbCopy)
        print(f"       published database copy -> {dbCopy} ({sizeMb:.1f} MB)")
    else:
        if os.path.exists(dbCopy):
            os.remove(dbCopy)
        print(f"       database is {sizeMb:.0f} MB (> {MAX_PUBLISH_MB} MB) — kept LOCAL only at {args.db}")
        print("       (open it in DB Browser / geopandas; the web app runs on the exported aggregates + sampled records)")

    print(f"\nDone in {time.time() - started:.1f}s -> {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
