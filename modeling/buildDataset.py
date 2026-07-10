"""Assemble a modelling dataset of individual SALE transactions from the LVR
season folders (downloaded by fetchHistory.py).

Reads only the sale main files (all needed features live there — no sub-tables),
de-duplicates on the 編號 serial (newest season wins), keeps housing sales with a
plausible transaction date and a unit price, and writes a compact parquet.

    python modeling/buildDataset.py --seasons-dir sourceData

Output: modeling/data/sales.parquet  (one row per sale transaction).
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from dataPipeline import valueMappings as vm  # noqa: E402
from dataPipeline.chineseNumeralParser import parseFloorCount  # noqa: E402
from dataPipeline.dealFlags import dealFlags  # noqa: E402
from dataPipeline.districtNames import toEnglish  # noqa: E402
from dataPipeline.csvLoader import (  # noqa: E402
    MAX_TXN_YEAR, MIN_TXN_YEAR, PING_PER_M2, _clean, boolInt, toFloat, toInt,
)
from dataPipeline.rocDateParser import buildingAgeYears, parseRocDate  # noqa: E402


def _seasonFolders(seasonsDir):
    out = []
    for name in os.listdir(seasonsDir):
        path = os.path.join(seasonsDir, name)
        if os.path.isdir(path) and os.path.exists(os.path.join(path, "manifest.csv")):
            out.append((name, path))

    def key(it):
        try:
            roc, q = it[0].upper().split("S")
            return (int(roc), int(q))
        except Exception:
            return (0, 0)

    out.sort(key=key, reverse=True)   # newest first, so the newest disclosure wins dedup
    return out


def _rows(path, code, cityEn, seen):
    df = pd.read_csv(path, skiprows=[1], dtype=str, keep_default_na=False,
                     encoding="utf-8-sig", on_bad_lines="skip", quoting=csv.QUOTE_NONE)
    out = []
    for r in df.to_dict("records"):
        serial = _clean(r.get("編號"))
        if serial and serial in seen:
            continue
        if serial:
            seen.add(serial)
        target = vm.mapTargetType(r.get("交易標的"))
        if target not in vm.HOUSING_TARGETS:
            continue
        saleDate = parseRocDate(r.get("交易年月日"))
        year = int(saleDate[:4]) if saleDate else None
        if year is None or not (MIN_TXN_YEAR <= year <= MAX_TXN_YEAR):
            continue
        unit = toFloat(r.get("單價元平方公尺"))
        if not unit or unit <= 0:
            continue                         # need a unit price to model
        buildArea = toFloat(r.get("建物移轉總面積平方公尺"))
        parkArea = toFloat(r.get("車位移轉總面積平方公尺")) or 0.0
        living = max((buildArea or 0) - parkArea, 0.0)
        landArea = toFloat(r.get("土地移轉總面積平方公尺"))
        mainArea = toFloat(r.get("主建物面積"))     # net of common area -> 公設 loading signal
        completion = parseRocDate(r.get("建築完成年月"))
        districtZh = _clean(r.get("鄉鎮市區"))
        districtEn = toEnglish(districtZh) or districtZh or "unknown"
        # Offline "geocode": pull the road/段 out of the address as a sub-district
        # location key (there are no coordinates in the LVR data).
        addr = _clean(r.get("土地位置建物門牌"))
        tail = addr.split(districtZh)[-1] if districtZh and districtZh in addr else addr
        mroad = re.search(r"([一-鿿]{1,10}?(?:路|街|大道))([一二三四五六七八九十]+段)?", tail)
        road = (mroad.group(1) + (mroad.group(2) or "")) if mroad else ""
        mainUseZh = _clean(r.get("主要用途"))
        materialZh = _clean(r.get("主要建材"))
        related, _cancelled, addition = dealFlags(_clean(r.get("備註")))
        out.append({
            "saleYear": year, "saleMonth": int(saleDate[5:7]),
            "cityCode": code, "cityEn": cityEn, "districtZh": districtZh,
            "districtEn": districtEn,
            "roadKey": (districtEn + "|" + road) if road else districtEn,
            "unitPricePerM2": unit,
            "totalPrice": toInt(r.get("總價元")),
            "livingAreaPing": round(living * PING_PER_M2, 3) if living else None,
            "landAreaPing": round(landArea * PING_PER_M2, 3) if landArea else None,
            "mainBuildingRatio": round(mainArea / buildArea, 4) if (mainArea and buildArea) else None,
            "buildingAgeYears": buildingAgeYears(completion, saleDate),
            "transferFloor": parseFloorCount(r.get("移轉層次")),
            "totalFloors": parseFloorCount(r.get("總樓層數")),
            "bedrooms": toInt(r.get("建物現況格局-房")),
            "livingRooms": toInt(r.get("建物現況格局-廳")),
            "bathrooms": toInt(r.get("建物現況格局-衛")),
            "hasCompartments": boolInt(r.get("建物現況格局-隔間")),
            "hasManagementOrg": boolInt(r.get("有無管理組織")),
            "hasParking": int(parkArea > 0 or target in vm.PARKING_TARGETS),
            "hasElevator": (lambda b: None if b is None else int(b))(vm.mapBool(r.get("電梯"))),
            "buildingType": vm.mapBuildingType(r.get("建物型態")),
            "mainUse": vm.mapMainUse(mainUseZh),
            "mainMaterial": vm.mapMainMaterial(materialZh),
            "relatedPartyDeal": related, "hasAddition": addition,
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the transaction modelling dataset.")
    ap.add_argument("--seasons-dir", default=os.path.join(os.path.dirname(HERE), "sourceData"))
    ap.add_argument("--out", default=os.path.join(HERE, "data", "sales.parquet"))
    ap.add_argument("--txn-suffix", default="a", choices=["a", "b", "c"],
                    help="a=sale (default), b=pre-sale, c=rental")
    args = ap.parse_args()

    seasons = _seasonFolders(args.seasons_dir)
    kind = {"a": "sales", "b": "pre-sales", "c": "rentals"}[args.txn_suffix]
    print(f"Reading {len(seasons)} seasons {seasons[-1][0]}..{seasons[0][0]} ({kind}, newest-first, de-duplicated)")
    seen, rows = set(), []
    for i, (name, path) in enumerate(seasons, 1):
        before = len(rows)
        for code, (en, _zh, _reg) in vm.CITY_BY_CODE.items():
            f = os.path.join(path, f"{code}_lvr_land_{args.txn_suffix}.csv")
            if os.path.exists(f):
                rows.extend(_rows(f, code, en, seen))
        print(f"  [{i}/{len(seasons)}] {name}: +{len(rows) - before:,} {kind}  (total {len(rows):,})")

    df = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    df.to_parquet(args.out, index=False)

    ym = df["saleYear"].astype(str) + "-" + df["saleMonth"].astype(str).str.zfill(2)
    print(f"\nWrote {len(df):,} {kind} -> {args.out} ({os.path.getsize(args.out) / 1e6:.1f} MB)")
    print(f"  transaction months: {ym.min()} .. {ym.max()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
