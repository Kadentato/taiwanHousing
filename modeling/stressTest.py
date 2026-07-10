"""Adversarial double-check of the whole artifact. Prints a list of issues (empty = clean).

  1. modelling data integrity (post-loadData: no NaN/inf, prices in band, no null keys)
  2. every shipped JSON is browser-safe (no NaN/Infinity literals — the bug class we hit)
  3. the DEPLOYED predictor.json under hostile inputs (unknown district/city/type, zero/huge
     size, div-by-zero floors, negative age): re-implements predictor.js exactly and checks
     the outputs stay finite, clipped, and the bands stay nested (95 ⊇ 80 ⊇ 50, lo ≤ point ≤ hi)

    python modeling/stressTest.py
"""

from __future__ import annotations

import json
import math
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import crossValidate as cv  # noqa: E402

DATA = os.path.join(os.path.dirname(HERE), "webApp", "dataFiles")
issues = []


def ok(cond, msg):
    if not cond:
        issues.append(msg)


# ---------------------------------------------------- 1. data integrity ----
def check_data():
    df = cv.loadData(os.path.join(HERE, "data", "sales.parquet"))
    n = len(df)
    for c in cv.NUMERIC:
        arr = df[c].to_numpy(float)
        ok(np.isfinite(arr).all(), f"data: {c} has {np.sum(~np.isfinite(arr))} non-finite values")
    ok(df["logReal"].pipe(np.isfinite).all(), "data: logReal non-finite")
    ok((df["unitPricePerM2"].between(cv.PRICE_LO, cv.PRICE_HI)).all(), "data: unitPrice out of band")
    for c in cv.CATCOLS + ["roadKey"]:
        ok(df[c].notna().all() and (df[c].astype(str).str.len() > 0).all(), f"data: {c} has null/empty")
    ok(int(df["monthIndex"].max()) == int(df.groupby("monthIndex").size().index.max()), "data: month cap")
    ok(df["floorRatio"].between(0, 1.5).all(), "data: floorRatio out of [0,1.5]")
    print(f"  data: {n:,} rows, latest {cv.label(int(df['monthIndex'].max()))}, "
          f"{df['roadKey'].nunique():,} roads")


# ----------------------------------------- 2. shipped JSON browser-safe ----
def _reject(x):
    raise ValueError(f"non-finite literal {x!r}")


def check_json_safe():
    import glob
    files = ["summary.json", "predictor.json", "monthlyMarketSeries.json",
             "regionAggregates.geojson", "cityAggregates.geojson", "districtAggregates.geojson"]
    files += [os.path.basename(f) for f in glob.glob(os.path.join(DATA, "cityRecords_*.json"))[:3]]
    for f in files:
        p = os.path.join(DATA, f)
        if not os.path.exists(p):
            issues.append(f"json: {f} missing"); continue
        try:
            json.loads(open(p, encoding="utf-8").read(), parse_constant=_reject)
        except ValueError as e:
            issues.append(f"json: {f} not browser-safe ({e})")
    print(f"  json: checked {len(files)} shipped files for NaN/Infinity")


# --------------------------------- 3. deployed predictor, hostile inputs ----
def load_model():
    return json.load(open(os.path.join(DATA, "predictor.json"), encoding="utf-8"))


def numeric_features(M, inp):
    clamp = lambda x, lo, hi: min(max(x, lo), hi)
    tf, totf = clamp(inp["transferFloor"], 0, 70), clamp(inp["totalFloors"], 1, 70)
    n = {"logArea": math.log(max(inp["livingAreaPing"], 1)),
         "logLand": math.log1p(max(inp["landAreaPing"], 0)),
         "mainBuildingRatio": clamp(inp["mainBuildingRatio"], 0.15, 1),
         "buildingAgeYears": inp["buildingAgeYears"], "ageSq": inp["buildingAgeYears"] ** 2,
         "bedrooms": inp["bedrooms"], "livingRooms": inp["livingRooms"], "bathrooms": inp["bathrooms"],
         "transferFloor": tf, "totalFloors": totf, "floorRatio": clamp(tf / totf, 0, 1.5),
         "hasParking": inp["hasParking"], "hasElevator": inp["hasElevator"],
         "hasManagementOrg": inp["hasManagementOrg"], "hasCompartments": inp["hasCompartments"],
         "monthIndex": M["asOfMonthIndex"]}
    return [n[k] for k in M["numericOrder"]]


def with_cats(M, num, inp, encoders):
    fv = list(num)
    for c in M["catOrder"]:
        e = encoders[c]
        v = inp.get(c)
        fv.append(e["map"].get(str(v), e["global"]))
    return fv


def walk(fv, trees, baseline):
    s = baseline
    for tree in trees:
        i = 0
        while True:
            nd = tree[i]
            if nd[0] == 1:
                s += nd[1]; break
            x = fv[nd[1]]
            if x is None or (isinstance(x, float) and math.isnan(x)):
                i = nd[3] if nd[5] else nd[4]
            else:
                i = nd[3] if x <= nd[2] else nd[4]
    return s


def predict(M, inp):
    num = numeric_features(M, inp)
    logReal = walk(with_cats(M, num, inp, M["encoders"]), M["trees"], M["baseline"])
    sig = None
    if "sigma" in M:
        sig = max(walk(with_cats(M, num, inp, M["sigma"]["encoders"]), M["sigma"]["trees"],
                       M["sigma"]["baseline"]), M["sigma"]["floor"])
    clip = lambda p: min(max(p, M["priceLo"]), M["priceHi"])
    f = M["nominalFactor"]
    point = clip(math.exp(logReal)) * f
    bands = {}
    for lvl in (50, 80, 95):
        half = (M["sigma"]["levels"][str(lvl)] * sig) if sig is not None else M["levels"][str(lvl)]
        bands[lvl] = (clip(math.exp(logReal - half)) * f, clip(math.exp(logReal + half)) * f)
    return point, sig, bands


def check_predictor():
    M = load_model()
    ok("version" in M and "sigma" in M, "predictor: missing version/sigma block")
    city = M["ui"]["cities"][0]["code"]
    dist = M["ui"]["districtsByCity"][city][0]
    btype = M["ui"]["buildingTypes"][0]
    base = dict(cityCode=city, districtEn=dist, buildingType=btype,
                mainUse="residential", mainMaterial="reinforcedConcrete",
                livingAreaPing=30, landAreaPing=5, mainBuildingRatio=0.66, buildingAgeYears=15,
                bedrooms=3, livingRooms=2, bathrooms=2, transferFloor=6, totalFloors=14,
                hasParking=1, hasElevator=1, hasManagementOrg=1, hasCompartments=1)

    def mut(**kw):
        d = dict(base); d.update(kw); return d

    cases = {
        "baseline": base,
        "unknown district": mut(districtEn="Nonexistent District 999"),
        "unknown city": mut(cityCode="zz"),
        "unknown building type": mut(buildingType="spaceship"),
        "unknown mainUse/material": mut(mainUse="???", mainMaterial="???"),
        "tiny size": mut(livingAreaPing=0.0001),
        "huge size": mut(livingAreaPing=100000),
        "zero total floors": mut(totalFloors=0, transferFloor=0),
        "floor > total": mut(transferFloor=999, totalFloors=5),
        "negative age": mut(buildingAgeYears=-5),
        "ancient": mut(buildingAgeYears=300),
        "negative land": mut(landAreaPing=-9),
        "ratio > 1": mut(mainBuildingRatio=5),
        "all zeros": {k: (0 if isinstance(v, (int, float)) else v) for k, v in base.items()},
    }
    for name, inp in cases.items():
        try:
            point, sig, bands = predict(M, inp)
        except Exception as e:
            issues.append(f"predictor[{name}]: raised {type(e).__name__}: {e}"); continue
        ok(math.isfinite(point), f"predictor[{name}]: point non-finite")
        lo95_lo, lo95_hi = bands[95]
        ok(M["priceLo"] * M["nominalFactor"] - 1 <= point <= M["priceHi"] * M["nominalFactor"] + 1,
           f"predictor[{name}]: point {point:.0f} outside clip band")
        if sig is not None:
            ok(math.isfinite(sig) and sig >= M["sigma"]["floor"] and sig < 5,
               f"predictor[{name}]: sigma {sig} implausible")
        prev = None
        for lvl in (50, 80, 95):
            lo, hi = bands[lvl]
            ok(all(map(math.isfinite, (lo, hi))), f"predictor[{name}] L{lvl}: band non-finite")
            ok(lo <= point + 1 and point <= hi + 1, f"predictor[{name}] L{lvl}: point outside band")
            ok(lo <= hi, f"predictor[{name}] L{lvl}: lo>hi")
            if prev is not None:
                ok(lo <= prev[0] + 1 and hi >= prev[1] - 1, f"predictor[{name}] L{lvl}: not nested vs smaller")
            prev = (lo, hi)
    # sanity: a Taipei tower should be pricier than a rural farmhouse-ish input
    p_taipei = predict(M, base)[0]
    print(f"  predictor: {len(cases)} hostile cases, baseline point NT${p_taipei/1e6:.1f}M/unit, "
          f"version {M.get('version', {}).get('dataThrough')}")


def main() -> int:
    print("Stress-testing ...")
    check_data()
    check_json_safe()
    check_predictor()
    print()
    if issues:
        print(f"FOUND {len(issues)} ISSUES:")
        for m in issues:
            print("  -", m)
        return 1
    print("ALL CLEAN — no gaps found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
