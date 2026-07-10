"""Value a CSV of houses: point price + a reasonable price range for each.

Trains a gradient-boosted price model on all history (constant 2021 NT$;
`--model hedonicDistrict` for the linear alternative), calibrates split-conformal
prediction intervals on the most recent months (out-of-sample, so the band
reflects real current-market error), then scores an input CSV and writes per-house
estimates in that period's nominal NT$:

  * predictedUnitPrice / predictedTotalPrice  - the single best guess
  * an 80% range   - the "reasonable" band (4 of 5 sales land inside)
  * a 95% range    - the high-confidence band

    python modeling/predictPrices.py --template          # write a blank input template
    python modeling/predictPrices.py --in houses.csv --out valued.csv

Input columns (header row; case-insensitive; missing optional ones are imputed):
  location   : cityEn OR cityCode OR cityZh  +  districtZh OR districtEn
  size/age   : livingAreaPing (or areaM2)  ,  buildingAgeYears
  optional   : transferFloor, totalFloors, bedrooms, livingRooms, bathrooms,
               hasParking, hasElevator, hasManagementOrg, hasCompartments,
               landAreaPing, mainBuildingRatio, buildingType, mainUse, mainMaterial
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                       # for crossValidate
sys.path.insert(0, os.path.dirname(HERE))      # for dataPipeline

import crossValidate as cv                      # noqa: E402
from dataPipeline import valueMappings as vm    # noqa: E402
from dataPipeline.districtNames import toEnglish  # noqa: E402
from dataPipeline.inflation import CPI_BASE_YEAR, cpiIndex  # noqa: E402

NUM_IMPUTE = ["livingAreaPing", "buildingAgeYears", "bedrooms", "livingRooms",
              "bathrooms", "transferFloor", "totalFloors", "mainBuildingRatio"]
BINARY = ["hasParking", "hasElevator", "hasManagementOrg", "hasCompartments"]
CAT_MAP = {"buildingType": vm.mapBuildingType, "mainUse": vm.mapMainUse,
           "mainMaterial": vm.mapMainMaterial}
LEVELS_OUT = [(50, 0.50), (80, 0.20), (95, 0.05)]   # most-likely / reasonable / high-confidence
CAL_MONTHS = 3                                   # recent months used to calibrate the band
SIG_FLOOR = 0.03                                 # min per-property difficulty (log units)

EN2CODE = {en: c for c, (en, _zh, _r) in vm.CITY_BY_CODE.items()}
ZH2CODE = {}
for _c, (_en, _zh, _r) in vm.CITY_BY_CODE.items():
    ZH2CODE[_zh] = _c
    ZH2CODE[_zh.replace("臺", "台")] = _c


# --------------------------------------------------------------- train + fit ---
def trainModel(df, asOf, trainCap, modelName):
    """Fit the point model on ALL history through `asOf` (most current), then
    calibrate the conformal band on the most recent CAL_MONTHS predicted
    out-of-sample (one-step-ahead, matching how the input houses are valued)."""
    cats = {"city": sorted(df["cityCode"].unique()), "type": sorted(df["buildingType"].unique()),
            "use": sorted(df["mainUse"].unique()), "material": sorted(df["mainMaterial"].unique()),
            "district": sorted(df["districtEn"].unique())}

    def cap(d):
        return d.sample(trainCap, random_state=0) if len(d) > trainCap else d

    pointPredict = cv.makePredictor(modelName, cap(df), cats)       # sub -> log real price
    calStart = asOf - CAL_MONTHS + 1
    calib = df[(df["monthIndex"] >= calStart) & (df["monthIndex"] <= asOf)]
    calibPredict = cv.makePredictor(modelName, cap(df[df["monthIndex"] < calStart]), cats)
    resid = calib["logReal"].to_numpy() - calibPredict(calib)
    q = {lvl: cv.conformalQ(resid, alpha) for lvl, alpha in LEVELS_OUT}

    # Locally-weighted (adaptive) band: a difficulty model sigma(x) scales the width
    # per property, so standard homes get a tighter range and oddballs a wider one.
    # Split calib: A fits sigma, B calibrates the normalized quantile.
    absR = np.abs(resid)
    idx = np.random.RandomState(1).permutation(len(calib))
    A, B = idx[: len(idx) // 2], idx[len(idx) // 2:]
    calA = calib.iloc[A]
    encSig = {c: cv.targetEncode(calA[c], absR[A]) for c in cv.CATCOLS}
    sigModel = HistGradientBoostingRegressor(max_iter=140, learning_rate=0.1, max_leaf_nodes=31,
                                             min_samples_leaf=200, random_state=0)
    sigModel.fit(cv.gbmFeatures(calA, encSig), absR[A])
    sB = np.clip(sigModel.predict(cv.gbmFeatures(calib.iloc[B], encSig)), SIG_FLOOR, None)
    qAdapt = {lvl: cv.conformalQ(absR[B] / sB, alpha) for lvl, alpha in LEVELS_OUT}

    def sigmaFn(sub):
        return np.clip(sigModel.predict(cv.gbmFeatures(sub, encSig)), SIG_FLOOR, None)

    return pointPredict, cats, q, qAdapt, sigmaFn, len(calib)


# ------------------------------------------------------------ input handling ---
def _lc(raw):
    return {c.lower().strip(): c for c in raw.columns}


def _col(raw, lc, *names):
    for n in names:
        if n.lower() in lc:
            return raw[lc[n.lower()]]
    return None


def _resolveCity(raw, lc):
    code = _col(raw, lc, "cityCode", "city_code")
    if code is not None:
        return code.astype(str).str.strip()
    out = []
    en = _col(raw, lc, "cityEn", "city", "cityName")
    zh = _col(raw, lc, "cityZh", "cityNameZh")
    for i in range(len(raw)):
        v = None
        if en is not None:
            v = EN2CODE.get(str(en.iloc[i]).strip())
        if v is None and zh is not None:
            v = ZH2CODE.get(str(zh.iloc[i]).strip())
        if v is None:
            raise ValueError(f"row {i}: could not resolve city from input; give cityEn "
                             f"(e.g. 'Taipei City'), cityCode (a-x), or cityZh (臺北市)")
        out.append(v)
    return pd.Series(out, index=raw.index)


def _resolveDistrict(raw, lc):
    de = _col(raw, lc, "districtEn", "district_en")
    dz = _col(raw, lc, "districtZh", "district_zh", "district")
    out = []
    for i in range(len(raw)):
        if dz is not None and str(dz.iloc[i]).strip():
            out.append(toEnglish(str(dz.iloc[i]).strip()))
        elif de is not None and str(de.iloc[i]).strip():
            out.append(str(de.iloc[i]).strip())
        else:
            out.append("unknown")
    return pd.Series(out, index=raw.index)


def _num(raw, lc, med, name, *aliases):
    s = _col(raw, lc, name, *aliases)
    if s is None:
        return pd.Series(np.full(len(raw), med), index=raw.index)
    return pd.to_numeric(s, errors="coerce").fillna(med)


def _binary(raw, lc, name):
    s = _col(raw, lc, name)
    if s is None:
        return pd.Series(np.zeros(len(raw)), index=raw.index)
    truthy = {"1", "1.0", "y", "yes", "true", "t", "有", "有電梯", "有管理組織"}
    return s.astype(str).str.strip().str.lower().isin(truthy).astype(float)


def _category(raw, lc, name, mapper, cats, default):
    s = _col(raw, lc, name)
    if s is None:
        return pd.Series([default] * len(raw), index=raw.index)
    out = []
    for v in s.fillna(""):
        v = str(v).strip()
        if v in cats:
            out.append(v)
        else:
            m = mapper(v)
            out.append(m if m is not None else default)
    return pd.Series(out, index=raw.index)


def prepareInput(raw, df, cats, asOf):
    lc = _lc(raw)
    med = {c: float(df[c].median()) for c in NUM_IMPUTE}
    mode = {c: df[c].mode().iloc[0] for c in CAT_MAP}
    X = pd.DataFrame(index=raw.index)
    X["cityCode"] = _resolveCity(raw, lc)
    X["districtEn"] = _resolveDistrict(raw, lc)

    # area accepts ping directly or m^2
    areaM2 = _col(raw, lc, "livingAreaM2", "areaM2", "buildingAreaM2")
    if _col(raw, lc, "livingAreaPing", "areaPing", "ping") is not None:
        X["livingAreaPing"] = _num(raw, lc, med["livingAreaPing"], "livingAreaPing", "areaPing", "ping")
    elif areaM2 is not None:
        X["livingAreaPing"] = pd.to_numeric(areaM2, errors="coerce").fillna(
            med["livingAreaPing"] / cv.M2_PER_PING) * cv.M2_PER_PING
    else:
        X["livingAreaPing"] = med["livingAreaPing"]

    X["buildingAgeYears"] = _num(raw, lc, med["buildingAgeYears"], "buildingAgeYears", "age")
    X["bedrooms"] = _num(raw, lc, med["bedrooms"], "bedrooms", "rooms", "房")
    X["livingRooms"] = _num(raw, lc, med["livingRooms"], "livingRooms", "廳")
    X["bathrooms"] = _num(raw, lc, med["bathrooms"], "bathrooms", "衛")
    X["transferFloor"] = _num(raw, lc, med["transferFloor"], "transferFloor", "floor").clip(0, 70)
    X["totalFloors"] = _num(raw, lc, med["totalFloors"], "totalFloors", "floors").clip(1, 70)
    X["mainBuildingRatio"] = _num(raw, lc, med["mainBuildingRatio"], "mainBuildingRatio").clip(0.15, 1.0)
    X["landAreaPing"] = _num(raw, lc, 0.0, "landAreaPing", "landPing")
    for b in BINARY:
        X[b] = _binary(raw, lc, b)
    for name, mapper in CAT_MAP.items():
        X[name] = _category(raw, lc, name, mapper, cats["type" if name == "buildingType" else
                                                        "use" if name == "mainUse" else "material"],
                            mode[name])
    # derived features (identical to crossValidate.loadData)
    X["logArea"] = np.log(X["livingAreaPing"].clip(lower=1.0))
    X["logLand"] = np.log1p(X["landAreaPing"].clip(lower=0.0))
    X["floorRatio"] = (X["transferFloor"] / X["totalFloors"]).clip(0.0, 1.5)
    X["ageSq"] = X["buildingAgeYears"] ** 2
    X["monthIndex"] = asOf
    return X


# ------------------------------------------------------------------- score ----
def score(X, pointPredict, q, qAdapt, sigmaFn, asOfYear):
    logReal = pointPredict(X)
    sig = sigmaFn(X) if sigmaFn is not None else None          # per-property difficulty
    factor = cpiIndex(asOfYear) / cpiIndex(CPI_BASE_YEAR)      # real 2021 -> nominal asOf year
    areaM2 = X["livingAreaPing"].to_numpy() * cv.M2_PER_PING
    out = pd.DataFrame(index=X.index)
    out["cityEn"] = X["cityCode"].map(lambda c: vm.CITY_BY_CODE.get(c, (c,))[0])
    out["districtEn"] = X["districtEn"].values
    out["ping"] = X["livingAreaPing"].round(1).values
    out["age"] = X["buildingAgeYears"].round(0).astype(int).values

    def unit(logv):
        return np.round(cv.clipExp(logv) * factor, -2)

    point = unit(logReal)
    out["predictedUnitPrice"] = point.astype(int)
    out["predictedTotalPrice"] = np.round(point * areaM2, -4).astype(int)
    for lvl, _a in LEVELS_OUT:
        half = qAdapt[lvl] * sig if sig is not None else np.full(len(X), q[lvl])   # per-property width
        lo, hi = unit(logReal - half), unit(logReal + half)
        out[f"unitLo{lvl}"] = lo.astype(int)
        out[f"unitHi{lvl}"] = hi.astype(int)
        out[f"totalLo{lvl}"] = np.round(lo * areaM2, -4).astype(int)
        out[f"totalHi{lvl}"] = np.round(hi * areaM2, -4).astype(int)
    return out


# ---------------------------------------------------------------- template ----
def writeTemplate(path):
    rows = [
        dict(cityEn="Taipei City", districtZh="大安區", livingAreaPing=28, buildingAgeYears=18,
             transferFloor=8, totalFloors=14, bedrooms=3, livingRooms=2, bathrooms=2,
             hasParking=1, hasElevator=1, hasManagementOrg=1, landAreaPing=5, mainBuildingRatio=0.66,
             buildingType="residentialTower", mainUse="residential", mainMaterial="reinforcedConcrete"),
        dict(cityEn="New Taipei City", districtZh="板橋區", livingAreaPing=22, buildingAgeYears=6,
             transferFloor=12, totalFloors=20, bedrooms=2, livingRooms=2, bathrooms=1,
             hasParking=1, hasElevator=1, hasManagementOrg=1, landAreaPing=3, mainBuildingRatio=0.62,
             buildingType="residentialTower", mainUse="residential", mainMaterial="reinforcedConcrete"),
        dict(cityEn="Kaohsiung City", districtZh="苓雅區", livingAreaPing=38, buildingAgeYears=28,
             transferFloor=4, totalFloors=7, bedrooms=3, livingRooms=2, bathrooms=2,
             hasParking=0, hasElevator=0, hasManagementOrg=0, landAreaPing=12, mainBuildingRatio=0.80,
             buildingType="walkUpApartment", mainUse="residential", mainMaterial="reinforcedBrick"),
    ]
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    print(f"Wrote input template ({len(rows)} example houses) -> {path}")


# ------------------------------------------------------------------- main -----
def main() -> int:
    ap = argparse.ArgumentParser(description="Value houses from a CSV: point price + range.")
    ap.add_argument("--in", dest="inp", help="input CSV of house attributes")
    ap.add_argument("--out", help="output CSV (default: <input>.valued.csv)")
    ap.add_argument("--data", default=os.path.join(HERE, "data", "sales.parquet"))
    ap.add_argument("--as-of", help="valuation month YYYY-MM (default: latest in data)")
    ap.add_argument("--model", default="gradientBoost",
                    choices=["gradientBoost", "hedonicDistrict"], help="prediction engine")
    ap.add_argument("--train-cap", type=int, default=400000)
    ap.add_argument("--template", action="store_true", help="write a blank input template and exit")
    args = ap.parse_args()

    if args.template:
        writeTemplate(args.out or os.path.join(HERE, "data", "housesTemplate.csv"))
        return 0
    if not args.inp:
        ap.error("give --in <houses.csv> (or --template to see the expected format)")

    df = cv.loadData(args.data)
    asOf = df["monthIndex"].max() if not args.as_of else cv.midx(*map(int, args.as_of.split("-")))
    asOfYear = cv.BASE_YEAR + asOf // 12
    print(f"Training {args.model} on {len(df):,} sales through {cv.label(df['monthIndex'].max())}; "
          f"valuing as of {cv.label(asOf)} (nominal {asOfYear} NT$)...")

    pointPredict, cats, q, qAdapt, sigmaFn, nCal = trainModel(df, asOf, args.train_cap, args.model)
    print(f"Locally-weighted band calibrated on {nCal:,} recent sales (width varies per home; "
          + "global reference: "
          + ", ".join(f"{lvl}% +-{(np.exp(q[lvl])-1)*100:.0f}%" for lvl, _a in LEVELS_OUT) + ")")

    raw = pd.read_csv(args.inp, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    X = prepareInput(raw, df, cats, asOf)
    out = score(X, pointPredict, q, qAdapt, sigmaFn, asOfYear)

    outPath = args.out or os.path.splitext(args.inp)[0] + ".valued.csv"
    out.to_csv(outPath, index=False, encoding="utf-8-sig")

    pd.set_option("display.width", 200, "display.max_columns", 30)
    print(f"\nValued {len(out)} houses (unit price NT$/m^2; total NT$):\n")
    show = out[["cityEn", "districtEn", "ping", "age", "predictedUnitPrice",
                "unitLo50", "unitHi50", "unitLo80", "unitHi80", "predictedTotalPrice"]]
    print(show.to_string(index=False))
    print(f"\n-> {outPath}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
