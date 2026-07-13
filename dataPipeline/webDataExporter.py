"""Export the database into static files the web app fetches (no backend).

Outputs into ``webApp/dataFiles/``:
  * districtAggregates.geojson / cityAggregates.geojson
    - geometry + per-transaction-type {count, median unit price, median total
      price, median ping} for the map (city + district; no region layer).
  * monthlyMarketSeries.json - month-by-month series at national / region / city
    level for the time charts.
  * cityRecords_<code>.json - trimmed per-record rows for client-side drill-down.
  * summary.json - hierarchy lists, tag catalog+counts, totals and data period.

"Housing" aggregates exclude land-only / parking-only transactions so the price
metrics describe actual dwellings.
"""

from __future__ import annotations

import datetime
import json
import math
import os
import sqlite3
from typing import Optional

import geopandas as gpd
import pandas as pd

from . import advancedStats
from . import districtRecords
from .inflation import CPI, CPI_BASE_YEAR
from .valueMappings import HOUSING_TARGETS

TYPES = ["sale", "presale", "rental"]
WGS84 = 4326
# The live web app loads records into the browser; cap per city so a large
# multi-year history stays fast. Map/chart aggregates + stats below are always
# computed on the FULL data — only the client's interactive record set is sampled.
MAX_RECORDS_PER_CITY = 2000   # client's interactive sample; keeps the page load light (~18 MB)
MAX_HEDONIC_ROWS = 200000
DEAL_FLAGS = ["relatedPartyDeal", "cancelledDeal", "hasAddition"]
MISSING_FIELDS = ["unitPricePerM2", "livingAreaPing", "bedrooms", "bathrooms",
                  "buildingAgeYears", "buildCompletionDate"]


def _finite(o):
    """Recursively replace NaN/inf with None so the JSON parses in the browser
    (Python's json tolerates NaN; the browser's JSON.parse does not)."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _finite(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_finite(v) for v in o]
    return o


def _med(series: pd.Series, ndigits: int = 0) -> Optional[float]:
    s = series.dropna()
    if s.empty:
        return None
    value = round(float(s.median()), ndigits)
    if math.isnan(value):
        return None
    return int(value) if ndigits == 0 else value


def _typeStats(group: pd.DataFrame) -> dict:
    """Flattened per-transaction-type stats for one geographic group."""
    props = {"totalCount": int(len(group))}
    for txn in TYPES:
        sub = group[group["transactionType"] == txn]
        props[f"{txn}Count"] = int(len(sub))
        props[f"{txn}MedUnitPrice"] = _med(sub["unitPricePerM2"])
        props[f"{txn}MedTotalPrice"] = _med(sub["totalPrice"])
        props[f"{txn}MedPing"] = _med(sub["livingAreaPing"], 1)
    return props


def _groupProps(housing: pd.DataFrame, keyCol: str) -> dict:
    return {key: _typeStats(grp) for key, grp in housing.groupby(keyCol)}


def _monthly(scope: pd.DataFrame) -> dict:
    out = {}
    for txn in TYPES:
        sub = scope[scope["transactionType"] == txn]
        sub = sub[sub["saleYear"].notna() & sub["saleMonth"].notna()]
        if sub.empty:
            continue
        ym = (sub["saleYear"].astype(int).astype(str) + "-"
              + sub["saleMonth"].astype(int).astype(str).str.zfill(2))
        sub = sub.assign(ym=ym)
        months = sorted(sub["ym"].unique())
        out[txn] = {
            "months": months,
            "count": [int((sub["ym"] == m).sum()) for m in months],
            "medUnitPrice": [_med(sub.loc[sub["ym"] == m, "unitPricePerM2"]) for m in months],
        }
    return out


def _writeGeoJson(rows: list, geometryByKey: dict, keyName: str, path: str):
    """Assemble a GeoDataFrame from property rows + a geometry lookup, write GeoJSON."""
    records, geoms = [], []
    for row in rows:
        geom = geometryByKey.get(row[keyName])
        if geom is None or geom.is_empty:
            continue
        records.append(row)
        geoms.append(geom)
    gdf = gpd.GeoDataFrame(records, geometry=geoms, crs=WGS84)
    if os.path.exists(path):
        os.remove(path)
    gdf.to_file(path, driver="GeoJSON")


# Only the columns the export actually reads — keeps a full-history houses table
# (millions of rows) from blowing up memory via SELECT *.
_HOUSE_COLS = ["houseId", "transactionType", "targetType", "cityId", "districtId",
               "saleYear", "saleMonth", "totalPrice", "parkingPrice", "unitPricePerM2",
               "livingAreaPing", "bedrooms", "bathrooms", "buildingType", "buildingAgeYears",
               "buildCompletionDate", "hasParking", "parkingType", "hasElevator",
               "hasManagementOrg", "relatedPartyDeal", "cancelledDeal", "hasAddition"]


def exportAll(conn: sqlite3.Connection, outDir: str, disclosure: Optional[str] = None) -> dict:
    os.makedirs(outDir, exist_ok=True)
    houses = pd.read_sql_query(f"SELECT {', '.join(_HOUSE_COLS)} FROM houses", conn)
    # Registration began 2012 Q3; a handful of rows carry data-entry dates (1900s /
    # far future). Keep only plausible transaction years so medians and the time
    # series aren't polluted or stretched across empty decades.
    houses = houses[houses["saleYear"].between(2012, datetime.date.today().year)].copy()
    # Trim the still-disclosing / future-dated recent tail to the latest COMPLETE month
    # (>= 50% of the recent-stable median monthly volume) so the time series doesn't
    # show a thin fake tail (e.g. stray future dates within the current year).
    _ym = houses["saleYear"].astype(int) * 12 + houses["saleMonth"].fillna(1).astype(int)
    _mc = _ym.value_counts().sort_index()
    _ref = _mc.iloc[-36:-6] if len(_mc) >= 42 else _mc
    houses = houses[_ym <= int(_mc[_mc >= 0.5 * _ref.median()].index.max())].copy()
    cities = pd.read_sql_query("SELECT * FROM cities", conn)
    regions = pd.read_sql_query("SELECT * FROM regions", conn)
    districts = pd.read_sql_query("SELECT * FROM districts", conn)
    # A few historical districts don't romanise; fall back to the Chinese name so
    # nameEn is never NaN (which would be invalid JSON in the browser).
    districts["nameEn"] = districts["nameEn"].fillna(districts["nameZh"]).fillna("")

    housing = houses[houses["targetType"].isin(HOUSING_TARGETS)].copy()

    # ---- geometry lookups ----
    cityGeom = {r.cityId: r.geometryWkt for r in cities.itertuples()}
    districtGeom = {r.districtId: r.geometryWkt for r in districts.itertuples()}
    fromWkt = lambda d: {k: gpd.GeoSeries.from_wkt([v]).iloc[0] if v else None for k, v in d.items()}
    cityGeom, districtGeom = fromWkt(cityGeom), fromWkt(districtGeom)

    cityMeta = {r.cityId: r for r in cities.itertuples()}

    # ---- district aggregates (points) ----
    dStats = _groupProps(housing, "districtId")
    dMeta = {r.districtId: r for r in districts.itertuples()}

    # Spatial autocorrelation of the district sale unit price (Moran's I + LISA).
    moran = advancedStats.districtMoran([
        {"districtId": did, "lat": dMeta[did].centroidLat, "lon": dMeta[did].centroidLon,
         "value": props.get("saleMedUnitPrice")}
        for did, props in dStats.items()
    ])
    lisaByDistrict = moran["lisa"] if moran else {}

    districtRows = []
    for districtId, props in dStats.items():
        meta = dMeta[districtId]
        city = cityMeta[meta.cityId]
        districtRows.append({
            "districtId": districtId, "districtZh": meta.nameZh, "districtEn": meta.nameEn,
            "cityCode": city.fileCode, "cityEn": city.nameEn, "regionId": city.regionId,
            "lisa": lisaByDistrict.get(districtId, "ns"),
            **props,
        })
    _writeGeoJson(districtRows, districtGeom, "districtId",
                  os.path.join(outDir, "districtAggregates.geojson"))

    # ---- city aggregates (polygons) ----
    cStats = _groupProps(housing, "cityId")
    cityRows = [{"cityId": cid, "cityCode": cityMeta[cid].fileCode, "cityEn": cityMeta[cid].nameEn,
                 "regionId": cityMeta[cid].regionId, **props} for cid, props in cStats.items()]
    _writeGeoJson(cityRows, cityGeom, "cityId", os.path.join(outDir, "cityAggregates.geojson"))

    # region grouping kept only for the monthly series below (the web map is city+district
    # now — the region layer was removed). Houses lack regionId directly, so derive via city.
    cityToRegion = {r.cityId: r.regionId for r in cities.itertuples()}
    housing = housing.assign(regionId=housing["cityId"].map(cityToRegion))

    # ---- monthly market series ----
    series = {"national": _monthly(housing),
              "regions": {int(rid): _monthly(grp) for rid, grp in housing.groupby("regionId")},
              "cities": {cityMeta[cid].fileCode: _monthly(grp)
                         for cid, grp in housing.groupby("cityId")}}
    with open(os.path.join(outDir, "monthlyMarketSeries.json"), "w", encoding="utf-8") as fh:
        json.dump(series, fh, ensure_ascii=False)

    # ---- per-district FULL record files (no sampling) — the web map lazy-loads each district's
    # complete set on drill-in. A geocoder (geocodeDoorplate.py) later overwrites the geocoded
    # cities' files with real coordinates. ----
    nD, nR, _ = districtRecords.exportAll(conn, outDir)
    recordsSampled = False

    # ---- summary / hierarchy / tags ----
    tagCounts = pd.read_sql_query(
        "SELECT t.slug, t.labelEn, t.category, COUNT(ht.houseId) AS n"
        " FROM tags t LEFT JOIN houseTags ht ON ht.tagId=t.tagId GROUP BY t.tagId", conn
    ).to_dict("records")
    ym = houses["saleYear"].astype(int) * 100 + houses["saleMonth"].fillna(1).astype(int)
    lo, hi = int(ym.min()), int(ym.max())
    period = {"minDate": f"{lo // 100}-{lo % 100:02d}", "maxDate": f"{hi // 100}-{hi % 100:02d}"}

    # sale-housing subset for model / quality stats (hedonic is sampled if huge)
    saleHousing = housing[housing["transactionType"] == "sale"]
    hedInput = saleHousing if len(saleHousing) <= MAX_HEDONIC_ROWS else saleHousing.sample(MAX_HEDONIC_ROWS, random_state=0)
    missingness = {c: {"missing": int(saleHousing[c].isna().sum()), "n": int(len(saleHousing))}
                   for c in MISSING_FIELDS}
    dealFlagCounts = {f: int(saleHousing[f].fillna(0).astype(int).sum()) for f in DEAL_FLAGS}
    dealFlagCounts["total"] = int(len(saleHousing))

    summary = {
        "period": period,
        "disclosure": disclosure,
        "totals": {t: int((houses["transactionType"] == t).sum()) for t in TYPES},
        "housingTotals": {t: int(((housing["transactionType"] == t)).sum()) for t in TYPES},
        "cpi": {"baseYear": CPI_BASE_YEAR, "index": CPI},
        "missingness": missingness,
        "dealFlagCounts": dealFlagCounts,
        "moran": {k: moran[k] for k in ("I", "p", "n")} if moran else None,
        "hedonic": advancedStats.hedonicRegression(hedInput),
        "recordsSampled": recordsSampled and MAX_RECORDS_PER_CITY,
        "regions": [{"regionId": r.regionId, "key": r.regionKey, "nameEn": r.nameEn}
                    for r in regions.itertuples()],
        "cities": [{"cityId": r.cityId, "cityCode": r.fileCode, "nameEn": r.nameEn,
                    "nameZh": r.nameZh, "regionId": r.regionId} for r in cities.itertuples()],
        "districts": [{"districtId": r.districtId, "cityId": r.cityId, "nameZh": r.nameZh, "nameEn": r.nameEn}
                      for r in districts.itertuples()],
        "tags": tagCounts,
    }
    with open(os.path.join(outDir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(_finite(summary), fh, ensure_ascii=False, allow_nan=False)

    return {"districts": len(districtRows), "cities": len(cityRows), "regions": len(regionRows),
            "recordFiles": recordsByCity["cityId"].nunique()}
