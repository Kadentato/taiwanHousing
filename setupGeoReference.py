"""ONE-TIME geo bootstrap. Run this once; afterwards every build is fully offline.

Downloads open Taiwan township boundaries (ronnywang/twgeojson, 2010 admin areas,
WGS84), then writes two bundled artifacts into ``geoReference/``:

* ``districtCentroids.csv``   - one row per (city, district) pair present in the
  LVR data, with an accurate centroid (computed in EPSG:3826 then back to 4326).
* ``townshipBoundaries.geojson`` - simplified township polygons for the 21
  counties in the data, used by the build to dissolve county/region choropleths
  and openable directly in QGIS.

Name matching handles the few stale-division cases (Taoyuan was 桃園縣 in 2010;
員林/頭份 were 鎮): a county alias plus core-name matching (strip 區/市/鎮/鄉).
Pairs with no township match (e.g. a district recorded as the bare city name)
fall back to the county centroid and are reported.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
import urllib.request

import geopandas as gpd
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dataPipeline.valueMappings import CITY_BY_CODE  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
GEO_DIR = os.path.join(HERE, "geoReference")
DEFAULT_SOURCE_DIR = r"C:\Users\Caden\Downloads\lvr_landcsv"
# Full-resolution townships (the pre-simplified variants ship degenerate rings
# that pyogrio rejects). We simplify ourselves below.
BOUNDARY_URL = "https://raw.githubusercontent.com/ronnywang/twgeojson/master/twtown2010.json"

TWD97 = 3826      # metric CRS for accurate centroids
WGS84 = 4326      # lat/lon for storage + web
SIMPLIFY_TOLERANCE = 0.0015   # ~150 m, shrinks the bundled boundary file

# County-name aliases: data county -> 2010-boundary county.
COUNTY_ALIAS = {"桃園市": "桃園縣"}
# Reverse map of normalised county name -> file code.
_CODE_BY_COUNTY = {zh.replace("台", "臺"): code for code, (_, zh, _) in CITY_BY_CODE.items()}


def _norm(text: str) -> str:
    return ("" if text is None else str(text)).strip().replace("台", "臺")


def _coreName(town: str) -> str:
    """Strip a trailing administrative-unit word so 中壢市/中壢區 -> 中壢."""
    town = _norm(town)
    for suffix in ("區", "市", "鎮", "鄉"):
        if len(town) > 1 and town.endswith(suffix):
            return town[:-1]
    return town


def dataDistrictPairs(sourceDir: str) -> "pd.Series":
    """Count rows per (cityCode, districtName) across all main CSVs."""
    counts: dict = {}
    for code, (_en, _zh, _reg) in CITY_BY_CODE.items():
        for suffix in ("a", "b", "c"):
            for path in glob.glob(os.path.join(sourceDir, f"{code}_lvr_land_{suffix}.csv")):
                df = pd.read_csv(path, skiprows=[1], dtype=str, keep_default_na=False)
                if "鄉鎮市區" not in df.columns:
                    continue
                for district in df["鄉鎮市區"]:
                    district = district.strip()
                    if district:
                        key = (code, district)
                        counts[key] = counts.get(key, 0) + 1
    return pd.Series(counts, name="rows")


def loadBoundaries(cachePath: str | None) -> gpd.GeoDataFrame:
    """Fetch (or reuse) the township GeoJSON and tag each row with a file code."""
    if cachePath and os.path.exists(cachePath):
        raw = open(cachePath, "rb").read()
    else:
        req = urllib.request.Request(BOUNDARY_URL, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=180).read()
        if cachePath:
            open(cachePath, "wb").write(raw)
    gdf = gpd.read_file(cachePath if cachePath else raw)
    gdf = gdf.set_crs(WGS84, allow_override=True)
    # Repair any self-intersecting / invalid rings before geometry ops.
    invalid = ~gdf.geometry.is_valid
    if invalid.any():
        gdf.loc[invalid, "geometry"] = gdf.loc[invalid, "geometry"].make_valid()
    gdf["countyNorm"] = gdf["county"].map(_norm)
    gdf["townNorm"] = gdf["town"].map(_norm)
    gdf["townCore"] = gdf["town"].map(_coreName)
    # Map boundary county -> data county (reverse the alias) -> file code.
    aliasToData = {v: k for k, v in COUNTY_ALIAS.items()}
    gdf["dataCounty"] = gdf["countyNorm"].map(lambda c: aliasToData.get(c, c))
    gdf["cityCode"] = gdf["dataCounty"].map(_CODE_BY_COUNTY)
    return gdf


def buildCentroids(boundaries: gpd.GeoDataFrame, pairs: "pd.Series"):
    """Return (centroidsDataFrame, unmatchedList)."""
    metric = boundaries.to_crs(TWD97)
    townCentroid = metric.geometry.centroid.to_crs(WGS84)
    boundaries = boundaries.assign(cLon=townCentroid.x, cLat=townCentroid.y)

    # Per-(code) lookup tables.
    byExact = {}
    byCore = {}
    for _, row in boundaries.iterrows():
        if not row["cityCode"]:
            continue
        byExact[(row["cityCode"], row["townNorm"])] = (row["cLat"], row["cLon"])
        byCore.setdefault((row["cityCode"], row["townCore"]), (row["cLat"], row["cLon"]))

    # County centroids (dissolve townships per code) for fallback.
    countyCentroids = {}
    dissolved = boundaries.dropna(subset=["cityCode"]).dissolve(by="cityCode")
    dCent = dissolved.to_crs(TWD97).geometry.centroid.to_crs(WGS84)
    for code, pt in zip(dissolved.index, dCent):
        countyCentroids[code] = (pt.y, pt.x)

    rows = []
    unmatched = []
    for (code, district), nRows in pairs.items():
        en, zh, _reg = CITY_BY_CODE[code]
        districtNorm = _norm(district)
        match = byExact.get((code, districtNorm))
        matchType = "townExact"
        if match is None:
            match = byCore.get((code, _coreName(district)))
            matchType = "townCore"
        if match is None:
            match = countyCentroids.get(code)
            matchType = "cityFallback"
            unmatched.append((zh, district, int(nRows)))
        lat, lon = match
        rows.append({
            "cityCode": code,
            "cityZh": zh,
            "cityEn": en,
            "districtName": district,
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "matchType": matchType,
            "rows": int(nRows),
        })
    return pd.DataFrame(rows), unmatched


def writeBoundaries(boundaries: gpd.GeoDataFrame, outPath: str) -> int:
    """Write simplified township polygons for the 21 data counties."""
    keep = boundaries.dropna(subset=["cityCode"]).copy()
    keep["geometry"] = keep.geometry.simplify(SIMPLIFY_TOLERANCE, preserve_topology=True)
    keep = keep[["cityCode", "dataCounty", "town", "geometry"]].rename(
        columns={"dataCounty": "county"}
    )
    keep.to_file(outPath, driver="GeoJSON")
    return len(keep)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate bundled geo reference files.")
    parser.add_argument("--source-dir", default=DEFAULT_SOURCE_DIR,
                        help="Folder containing the LVR CSVs (for district list).")
    parser.add_argument("--cache", default=None,
                        help="Optional path to cache/reuse the downloaded GeoJSON.")
    args = parser.parse_args()

    os.makedirs(GEO_DIR, exist_ok=True)
    print(f"Reading data districts from {args.source_dir} ...")
    pairs = dataDistrictPairs(args.source_dir)
    print(f"  {len(pairs)} distinct (city, district) pairs")

    print(f"Loading township boundaries ({BOUNDARY_URL.split('/')[-1]}) ...")
    boundaries = loadBoundaries(args.cache)
    print(f"  {len(boundaries)} townships; {boundaries['cityCode'].notna().sum()} in data counties")

    centroids, unmatched = buildCentroids(boundaries, pairs)
    centroidsPath = os.path.join(GEO_DIR, "districtCentroids.csv")
    centroids.sort_values(["cityCode", "districtName"]).to_csv(
        centroidsPath, index=False, encoding="utf-8-sig"
    )
    counts = centroids["matchType"].value_counts().to_dict()
    print(f"Wrote {centroidsPath} ({len(centroids)} rows) match types: {counts}")

    boundaryPath = os.path.join(GEO_DIR, "townshipBoundaries.geojson")
    nKept = writeBoundaries(boundaries, boundaryPath)
    sizeMb = os.path.getsize(boundaryPath) / 1e6
    print(f"Wrote {boundaryPath} ({nKept} townships, {sizeMb:.1f} MB)")

    print(f"\nUnmatched -> county centroid fallback: {len(unmatched)}")
    for zh, district, n in sorted(unmatched):
        print(f"   {zh} / {district}  ({n} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
