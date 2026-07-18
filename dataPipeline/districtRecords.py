"""Per-district FULL housing-sale records, written compact + gzipped for lazy loading.

The web map no longer ships a per-city *sample*; instead every district's complete set of
sales is written to ``webApp/dataFiles/districtRecords/<districtId>.json.gz`` and fetched only
when the user drills into that district. Format is positional to stay small:

    {"cols":[...field names...], "dict":{"buildingType":[...],"parkingType":[...],"targetType":[...]},
     "rows":[[v0,v1,...], ...]}

`lat`/`lon` are null unless a geocoder fills them (see geocodeDoorplate.py, which overwrites the
geocoded cities' files with real coordinates). districtId/cityCode/transactionType are implied by
the file (the app sets them on decode), so they aren't repeated per row.
"""
from __future__ import annotations

import gzip
import json
import os

import pandas as pd

from . import anomalyFilter

# Everything the dots, tooltip, records table and client-side filters need — nothing else.
COLS = ["lat", "lon", "unitPricePerM2", "totalPrice", "livingAreaPing", "bedrooms", "bathrooms",
        "buildingAgeYears", "buildingType", "parkingType", "hasParking", "hasElevator",
        "hasManagementOrg", "relatedPartyDeal", "cancelledDeal", "hasAddition",
        "saleYear", "saleMonth", "targetType"]
DICT_FIELDS = ["buildingType", "parkingType", "targetType"]
ROUND = {"lat": 6, "lon": 6, "livingAreaPing": 1, "buildingAgeYears": 1}
INT_FIELDS = ["unitPricePerM2", "totalPrice", "bedrooms", "bathrooms", "hasParking", "hasElevator",
              "hasManagementOrg", "relatedPartyDeal", "cancelledDeal", "hasAddition", "saleYear", "saleMonth"]
HOUSING = ("houseLand", "houseLandParking", "buildingOnly")


def _clean(v, ndigits=None, asInt=False):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if asInt:
        return int(round(float(v)))
    if ndigits is not None:
        return round(float(v), ndigits)
    return v


def writeOne(path, df):
    """Write one district's records (a DataFrame with the COLS columns; lat/lon optional)."""
    df = df.copy()
    for c in ("lat", "lon"):
        if c not in df.columns:
            df[c] = None
    dicts = {}
    for c in DICT_FIELDS:
        vals = sorted(str(v) for v in df[c].dropna().unique())
        idx = {v: i for i, v in enumerate(vals)}
        dicts[c] = vals
        df[c] = df[c].map(lambda v: idx.get(str(v)) if pd.notna(v) else None)
    rows = []
    for r in df[COLS].itertuples(index=False):
        d = dict(zip(COLS, r))
        row = []
        for c in COLS:
            if c in DICT_FIELDS:
                row.append(None if d[c] is None or pd.isna(d[c]) else int(d[c]))
            elif c in INT_FIELDS:
                row.append(_clean(d[c], asInt=True))
            else:
                row.append(_clean(d[c], ndigits=ROUND.get(c)))
        rows.append(row)
    payload = {"cols": COLS, "dict": dicts, "rows": rows}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def exportAll(conn, outDir):
    """Write every district's full sale-housing records (no coordinates — a geocoder overwrites the
    geocoded cities' files afterwards). Returns (nDistricts, totalRows, bytes)."""
    dst = os.path.join(outDir, "districtRecords")
    os.makedirs(dst, exist_ok=True)
    sel = [c for c in COLS if c not in ("lat", "lon")] + ["districtId"]
    ph = ",".join("?" * len(HOUSING))
    df = pd.read_sql_query(
        f"SELECT {','.join(sel)} FROM houses WHERE transactionType='sale' AND targetType IN ({ph})",
        conn, params=HOUSING)
    df = anomalyFilter.dropAnomalies(df)   # drop impossible/implausible sale records
    total, nb = 0, 0
    for did, grp in df.groupby("districtId"):
        path = os.path.join(dst, f"{int(did)}.json.gz")
        writeOne(path, grp)
        total += len(grp)
        nb += os.path.getsize(path)
    return df["districtId"].nunique(), total, nb
