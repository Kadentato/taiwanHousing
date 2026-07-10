"""How accurately - and how *confidently* - can we predict a property's price?

Given a property's metadata (location, size, age, floor, layout, ...) predict its
transaction price, validated strictly out-of-time: train only on sales *before* the
held-out month, then predict that month. Held to three principles:

  1. Inflation. Prices are deflated to constant 2021 NT$ (DGBAS CPI) so the time
     trend is real appreciation, not monetary inflation.
  2. A range, not a number. Split-conformal prediction intervals, width calibrated
     on the previous month predicted out-of-sample (one-step-ahead), then verified
     empirically (does a 95% range really contain 95% of sales?).
  3. Cross-validated. The held-out month rolls across a window so every number is a
     distribution, not one lucky month. Feb-2024 is the headline fold.

Models compared head-to-head:
  * globalMedian / cityTypeMedian - baselines (national, and city x type)
  * hedonicDistrict - linear hedonic + district fixed effects + city time trend
  * gradientBoost   - HistGradientBoosting on the same features (location target-
                      encoded), which captures interactions (floor x type, age x
                      district, size nonlinearity) the linear model cannot.

Reported: PART A point accuracy, PART B city-median accuracy, PART C interval
calibration + band width per model.

    python modeling/crossValidate.py
    python modeling/crossValidate.py --cv-from 2023-03 --train-cap 200000
"""

from __future__ import annotations

import argparse
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import scipy.sparse as sp  # noqa: E402
from sklearn.ensemble import HistGradientBoostingRegressor  # noqa: E402
from sklearn.linear_model import LinearRegression  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from dataPipeline.inflation import CPI_BASE_YEAR, cpiIndex  # noqa: E402

BASE_YEAR = 2012
PRICE_LO, PRICE_HI = 5000, 3_000_000          # plausible NT$/m^2 band (drops entry errors)
M2_PER_PING = 3.305785
NUMERIC = ["logArea", "logLand", "mainBuildingRatio", "buildingAgeYears", "ageSq",
           "bedrooms", "livingRooms", "bathrooms", "transferFloor", "totalFloors",
           "floorRatio", "hasParking", "hasElevator", "hasManagementOrg",
           "hasCompartments", "monthIndex"]
CATCOLS = ["cityCode", "districtEn", "buildingType", "mainUse", "mainMaterial"]
COVERAGE = (0.10, 0.20, 0.30)                  # point-accuracy bands
LEVELS = ((50, 0.50), (80, 0.20), (95, 0.05), (99, 0.01))   # interval confidence levels
POINT_MODELS = ["globalMedian", "cityTypeMedian", "hedonicDistrict", "gradientBoost"]
IV_MODELS = ["hedonicDistrict", "gradientBoost"]   # models that get a fitted predictor + intervals


def midx(year, month):
    return (year - BASE_YEAR) * 12 + (month - 1)


def label(idx):
    return f"{BASE_YEAR + idx // 12}-{idx % 12 + 1:02d}"


# ------------------------------------------------------------------- load ----
def loadData(path):
    df = pd.read_parquet(path)
    df["monthIndex"] = midx(df["saleYear"], df["saleMonth"])
    df = df[df["saleYear"] >= BASE_YEAR]
    df = df[(df["unitPricePerM2"] >= PRICE_LO) & (df["unitPricePerM2"] <= PRICE_HI)]
    # Drop the still-disclosing recent tail (LVR reveals transactions with a lag), so
    # "latest" is the most recent COMPLETE month, not a half-reported one. Any month
    # with < 50% of the recent-stable median volume is treated as not-yet-complete.
    counts = df.groupby("monthIndex").size().sort_index()
    ref = counts.iloc[-36:-6] if len(counts) >= 42 else counts
    completeMax = int(counts[counts >= 0.5 * ref.median()].index.max())
    df = df[df["monthIndex"] <= completeMax]
    df["totalFloors"] = df["totalFloors"].clip(lower=1, upper=70)
    df["transferFloor"] = df["transferFloor"].clip(lower=0, upper=70)
    for c in ["livingAreaPing", "buildingAgeYears", "bedrooms", "livingRooms",
              "bathrooms", "transferFloor", "totalFloors", "mainBuildingRatio"]:
        df[c] = df[c].fillna(df[c].median())
    for c in ["hasElevator", "hasParking", "hasManagementOrg", "hasCompartments"]:
        df[c] = df[c].fillna(0)
    df["landAreaPing"] = df["landAreaPing"].fillna(0.0)
    df["mainBuildingRatio"] = df["mainBuildingRatio"].clip(lower=0.15, upper=1.0)
    for c in ["buildingType", "mainUse", "mainMaterial"]:
        df[c] = df[c].fillna("other").replace("", "other")
    df["districtEn"] = df["districtEn"].fillna("unknown").replace("", "unknown")
    df["logArea"] = np.log(df["livingAreaPing"].clip(lower=1.0))
    df["logLand"] = np.log1p(df["landAreaPing"].clip(lower=0.0))
    df["floorRatio"] = (df["transferFloor"] / df["totalFloors"]).clip(lower=0.0, upper=1.5)
    df["ageSq"] = df["buildingAgeYears"] ** 2
    df["cpi"] = df["saleYear"].map(cpiIndex)
    df["realUnit"] = df["unitPricePerM2"] * (cpiIndex(CPI_BASE_YEAR) / df["cpi"])
    df["logReal"] = np.log(df["realUnit"])
    return df.reset_index(drop=True)


# --------------------------------------------- linear hedonic design + fit ----
def sparseDummies(series, cats):
    codes = pd.Categorical(series, categories=cats).codes
    ok = codes >= 0
    rows = np.nonzero(ok)[0]
    return sp.csr_matrix((np.ones(len(rows)), (rows, codes[ok])), shape=(len(series), len(cats)))


def design(sub, cats, useDistrict):
    num = sp.csr_matrix(sub[NUMERIC].to_numpy(float))
    cityD = sparseDummies(sub["cityCode"], cats["city"])
    typeD = sparseDummies(sub["buildingType"], cats["type"])
    useD = sparseDummies(sub["mainUse"], cats["use"])
    matD = sparseDummies(sub["mainMaterial"], cats["material"])
    month = sub["monthIndex"].to_numpy(float).reshape(-1, 1)
    cityTime = cityD.multiply(month).tocsr()
    blocks = [num, cityD, typeD, useD, matD, cityTime]
    if useDistrict:
        blocks.append(sparseDummies(sub["districtEn"], cats["district"]))
    return sp.hstack(blocks, format="csr")


def fitModel(train, cats, useDistrict):
    m = LinearRegression()
    m.fit(design(train, cats, useDistrict), train["logReal"].to_numpy())
    return m


def logPredict(model, sub, cats, useDistrict):
    return model.predict(design(sub, cats, useDistrict))


# ------------------------------------------- gradient boosting (nonlinear) ----
def targetEncode(keys, y, smoothing=20.0):
    """Smoothed mean-of-target per category (handles high-cardinality location)."""
    d = pd.DataFrame({"k": np.asarray(keys), "y": np.asarray(y, float)})
    stats = d.groupby("k")["y"].agg(["sum", "count"])
    gmean = float(d["y"].mean())
    enc = (stats["sum"] + gmean * smoothing) / (stats["count"] + smoothing)
    return enc.to_dict(), gmean


def gbmFeatures(sub, encoders):
    num = sub[NUMERIC].to_numpy(float)
    cat = np.column_stack([sub[c].map(encoders[c][0]).fillna(encoders[c][1]).to_numpy(float)
                           for c in CATCOLS])
    return np.hstack([num, cat])


def fitGBM(train):
    y = train["logReal"].to_numpy()
    encoders = {c: targetEncode(train[c], y) for c in CATCOLS}
    model = HistGradientBoostingRegressor(
        max_iter=350, learning_rate=0.07, max_leaf_nodes=63, min_samples_leaf=80,
        l2_regularization=1.0, early_stopping=True, n_iter_no_change=25,
        validation_fraction=0.1, random_state=0)
    model.fit(gbmFeatures(train, encoders), y)
    return model, encoders


def makePredictor(name, train, cats):
    """Fit `name` on `train`, return a function sub -> log-price predictions."""
    if name == "gradientBoost":
        model, enc = fitGBM(train)
        return lambda sub: model.predict(gbmFeatures(sub, enc))
    useD = (name == "hedonicDistrict")
    model = fitModel(train, cats, useD)
    return lambda sub: logPredict(model, sub, cats, useD)


def clipExp(logv):
    return np.clip(np.exp(logv), PRICE_LO, PRICE_HI)


def medianBaseline(train, test, keys, col="realUnit"):
    glob = train[col].median()
    pred = np.full(len(test), glob, float)
    if keys:
        table = train.groupby(keys)[col].median()
        idx = pd.MultiIndex.from_frame(test[keys]) if len(keys) > 1 else pd.Index(test[keys[0]])
        looked = table.reindex(idx).to_numpy()
        pred = np.where(np.isnan(looked), pred, looked)
    return pred


def conformalQ(resid, alpha):
    r = np.sort(np.abs(resid))
    n = len(r)
    k = min(int(np.ceil((n + 1) * (1 - alpha))), n)
    return float(r[k - 1])


# ---------------------------------------------------------------- metrics ----
def perTxn(actual, pred):
    actual = np.asarray(actual, float)
    pred = np.asarray(pred, float)
    ape = np.abs(pred - actual) / actual
    logErr = np.log(pred) - np.log(actual)
    y = np.log(actual)
    ssTot = float(((y - y.mean()) ** 2).sum())
    m = {"n": len(actual), "medape": float(np.median(ape)), "mape": float(ape.mean()),
         "rmseLog": float(np.sqrt((logErr ** 2).mean())),
         "r2log": 1.0 - float((logErr ** 2).sum()) / ssTot if ssTot else float("nan")}
    for b in COVERAGE:
        m[f"cov{int(b*100)}"] = float((ape <= b).mean())
    return m


def cityMedianErrors(test, actual, pred, minN):
    d = pd.DataFrame({"cityCode": test["cityCode"].to_numpy(), "actual": actual, "pred": pred})
    g = d.groupby("cityCode").agg(n=("actual", "size"), actMed=("actual", "median"),
                                  predMed=("pred", "median"))
    g = g[g["n"] >= minN]
    g["ape"] = (g["predMed"] - g["actMed"]).abs() / g["actMed"]
    return g


# ------------------------------------------------------------- one month -----
def evaluateMonth(df, T, cats, trainCap):
    test = df[df["monthIndex"] == T]
    if len(test) < 50:
        return None

    def cap(d):
        return d.sample(trainCap, random_state=0) if len(d) > trainCap else d

    trainFull = cap(df[df["monthIndex"] < T])
    actualReal = test["realUnit"].to_numpy(float)

    preds = {"globalMedian": medianBaseline(trainFull, test, []),
             "cityTypeMedian": medianBaseline(trainFull, test, ["cityCode", "buildingType"])}
    logStore = {}
    for name in IV_MODELS:
        lg = makePredictor(name, trainFull, cats)(test)
        logStore[name] = lg
        preds[name] = clipExp(lg)

    metrics = {m: perTxn(actualReal, p) for m, p in preds.items()}
    res = {"test": test, "actualReal": actualReal, "preds": preds, "metrics": metrics,
           "logStore": logStore}

    # ---- conformal intervals, calibrated one-step-ahead, for each fitted model ----
    calib = df[df["monthIndex"] == T - 1]
    if len(calib) >= 200:
        trainPrev = cap(df[df["monthIndex"] < T - 1])
        iv = {}
        for name in IV_MODELS:
            resid = calib["logReal"].to_numpy() - makePredictor(name, trainPrev, cats)(calib)
            iv[name] = {}
            for lvl, alpha in LEVELS:
                q = conformalQ(resid, alpha)
                lo = np.clip(np.exp(logStore[name] - q), PRICE_LO, PRICE_HI)
                hi = np.clip(np.exp(logStore[name] + q), PRICE_LO, PRICE_HI)
                iv[name][lvl] = {"q": q, "hiFac": float(np.exp(q)), "lo": lo, "hi": hi,
                                 "cov": float(((actualReal >= lo) & (actualReal <= hi)).mean())}
        res["intervals"] = iv
    return res


# ------------------------------------------------------------------- main ----
def main() -> int:
    ap = argparse.ArgumentParser(description="Cross-validate price prediction: linear vs gradient boosting.")
    ap.add_argument("--data", default=os.path.join(HERE, "data", "sales.parquet"))
    ap.add_argument("--holdout", default="2024-02")
    ap.add_argument("--cv-from", default="2023-03")
    ap.add_argument("--train-cap", type=int, default=200000)
    ap.add_argument("--min-n", type=int, default=20)
    args = ap.parse_args()

    df = loadData(args.data)
    cats = {"city": sorted(df["cityCode"].unique()), "type": sorted(df["buildingType"].unique()),
            "use": sorted(df["mainUse"].unique()), "material": sorted(df["mainMaterial"].unique()),
            "district": sorted(df["districtEn"].unique())}

    hy, hm = map(int, args.holdout.split("-"))
    cy, cm = map(int, args.cv_from.split("-"))
    holdoutIdx = midx(hy, hm)
    cvMonths = [t for t in range(midx(cy, cm), holdoutIdx + 1)
                if len(df[df["monthIndex"] == t]) >= 50]

    print(f"Dataset: {len(df):,} sales, {label(df['monthIndex'].min())}..{label(df['monthIndex'].max())}, "
          f"{len(cats['city'])} cities, {len(cats['district'])} districts")
    print(f"Constant {CPI_BASE_YEAR} NT$. Rolling CV over {len(cvMonths)} months "
          f"({label(cvMonths[0])}..{label(cvMonths[-1])}); holdout {args.holdout}, train-cap {args.train_cap:,}\n")

    agg = {m: [] for m in POINT_MODELS}
    ivAgg = {name: {lvl: [] for lvl, _ in LEVELS} for name in IV_MODELS}
    holdout = None
    for T in cvMonths:
        res = evaluateMonth(df, T, cats, args.train_cap)
        if res is None:
            continue
        for m in POINT_MODELS:
            agg[m].append(res["metrics"][m])
        if "intervals" in res:
            for name in IV_MODELS:
                for lvl, _ in LEVELS:
                    ivAgg[name][lvl].append(res["intervals"][name][lvl])
        if T == holdoutIdx:
            holdout = res
        print(f"  scored {label(T)}  (n={len(res['test']):,})")

    def cvMean(m, k):
        v = [d[k] for d in agg[m]]
        return float(np.mean(v)) if v else float("nan")

    # ---- PART A: point accuracy ----
    print("\n=== PART A - individual property price accuracy, real terms (rolling-CV mean) ===")
    print(f"{'model':<17}{'medAPE':>8}{'MAPE':>8}{'<=10%':>8}{'<=20%':>8}{'<=30%':>8}{'R2(log)':>9}")
    print("-" * 66)
    for m in POINT_MODELS:
        print(f"{m:<17}{cvMean(m,'medape')*100:>7.1f}%{cvMean(m,'mape')*100:>7.1f}%"
              f"{cvMean(m,'cov10')*100:>7.1f}%{cvMean(m,'cov20')*100:>7.1f}%"
              f"{cvMean(m,'cov30')*100:>7.1f}%{cvMean(m,'r2log'):>9.3f}")
    if holdout:
        print(f"\n--- {args.holdout} holdout (n={len(holdout['test']):,}) ---")
        for m in POINT_MODELS:
            d = holdout["metrics"][m]
            print(f"{m:<17}{d['medape']*100:>7.1f}%{d['mape']*100:>7.1f}%"
                  f"{d['cov10']*100:>7.1f}%{d['cov20']*100:>7.1f}%{d['cov30']*100:>7.1f}%{d['r2log']:>9.3f}")

    # ---- PART C: interval calibration + band width, per model ----
    print("\n=== PART C - prediction intervals: realized coverage & band width ===")
    for name in IV_MODELS:
        print(f"\n  [{name}]")
        print(f"  {'level':>6}{'target':>8}{'realized(CV)':>14}{'realized(hold)':>16}{'band width':>14}")
        for lvl, _ in LEVELS:
            covCv = np.mean([d["cov"] for d in ivAgg[name][lvl]]) if ivAgg[name][lvl] else float("nan")
            fac = np.mean([d["hiFac"] for d in ivAgg[name][lvl]]) if ivAgg[name][lvl] else float("nan")
            hCov = (holdout["intervals"][name][lvl]["cov"]
                    if holdout and "intervals" in holdout else float("nan"))
            print(f"  {lvl:>5}%{lvl:>7}%{covCv*100:>13.1f}%{hCov*100:>15.1f}%   +-{(fac-1)*100:>3.0f}%")

    # ---- PART B: city-median accuracy ----
    if holdout:
        print(f"\n=== PART B - {args.holdout} city-median accuracy (aggregated) ===")
        for name in IV_MODELS:
            g = cityMedianErrors(holdout["test"], holdout["actualReal"], holdout["preds"][name], args.min_n)
            print(f"  {name:<16} city-median MAPE across {len(g)} cities: {g['ape'].mean()*100:.2f}%")
        best = "gradientBoost"
        _examples(holdout, hy, best)
        _plots(agg, ivAgg, holdout, args.holdout)
    return 0


def _examples(holdout, year, name):
    from dataPipeline.valueMappings import CITY_BY_CODE
    test = holdout["test"].reset_index(drop=True)
    toNominal = cpiIndex(year) / cpiIndex(CPI_BASE_YEAR)
    point = np.exp(holdout["logStore"][name]) * toNominal
    lo = holdout["intervals"][name][80]["lo"] * toNominal
    hi = holdout["intervals"][name][80]["hi"] * toNominal
    actual = test["unitPricePerM2"].to_numpy(float)
    order = np.argsort(point)
    picks = [order[int(p * (len(order) - 1))] for p in (0.1, 0.35, 0.6, 0.85, 0.97)]
    print(f"\nExample {label(holdout['test']['monthIndex'].iloc[0])} properties "
          f"({name}, unit NT$/m^2, 80% range):")
    print(f"  {'city / district':<26}{'ping':>5}{'age':>4}{'actual':>10}{'predicted':>11}{'80% range':>21}{'in?':>5}")
    for i in picks:
        r = test.iloc[i]
        cityName = CITY_BY_CODE.get(r["cityCode"], (r["cityCode"],))[0]
        loc = f"{cityName}/{r['districtEn']}"[:25]
        inside = "yes" if lo[i] <= actual[i] <= hi[i] else "NO"
        print(f"  {loc:<26}{r['livingAreaPing']:>5.0f}{r['buildingAgeYears']:>4.0f}"
              f"{actual[i]:>10,.0f}{point[i]:>11,.0f}{lo[i]:>9,.0f}-{hi[i]:<8,.0f}{inside:>5}")


# ------------------------------------------------------------------ plots ----
def _plots(agg, ivAgg, holdout, holdoutLabel):
    out = os.path.join(HERE, "output")
    os.makedirs(out, exist_ok=True)

    def cvMean(m, k):
        return float(np.mean([d[k] for d in agg[m]])) if agg[m] else 0.0

    # 1) point median-APE by model
    fig, ax = plt.subplots(figsize=(6.6, 4))
    vals = [cvMean(m, "medape") * 100 for m in POINT_MODELS]
    ax.bar(POINT_MODELS, vals, color=["#cbd5e1", "#94a3b8", "#2563eb", "#16a34a"])
    ax.set_ylabel("median abs. % error"); ax.set_title("Typical miss per property (rolling-CV)")
    for i, v in enumerate(vals):
        ax.text(i, v, f"{v:.1f}%", ha="center", va="bottom", fontsize=9)
    ax.tick_params(axis="x", rotation=12)
    fig.tight_layout(); fig.savefig(os.path.join(out, "individualMedApe.png"), dpi=110); plt.close(fig)

    # 2) band width by model and level (the tightening we care about)
    fig, ax = plt.subplots(figsize=(6.6, 4))
    x = np.arange(len(LEVELS)); w = 0.38
    for j, name in enumerate(IV_MODELS):
        widths = [(np.mean([d["hiFac"] for d in ivAgg[name][lvl]]) - 1) * 100 if ivAgg[name][lvl] else 0
                  for lvl, _ in LEVELS]
        ax.bar(x + (j - 0.5) * w, widths, w, label=name, color=["#2563eb", "#16a34a"][j])
        for xi, v in zip(x + (j - 0.5) * w, widths):
            ax.text(xi, v, f"{v:.0f}%", ha="center", va="bottom", fontsize=8)
    ax.set_xticks(x); ax.set_xticklabels([f"{lvl}%" for lvl, _ in LEVELS])
    ax.set_ylabel("band half-width (+- %)"); ax.set_xlabel("confidence level")
    ax.set_title("Prediction-range width: linear vs gradient boosting"); ax.legend()
    fig.tight_layout(); fig.savefig(os.path.join(out, "bandWidthCompare.png"), dpi=110); plt.close(fig)

    # 3) coverage calibration (gradientBoost)
    fig, ax = plt.subplots(figsize=(6, 4))
    x = np.arange(len(LEVELS)); w = 0.35
    tgt = [lvl for lvl, _ in LEVELS]
    realized = [np.mean([d["cov"] for d in ivAgg["gradientBoost"][lvl]]) * 100
                if ivAgg["gradientBoost"][lvl] else 0 for lvl, _ in LEVELS]
    ax.bar(x - w / 2, tgt, w, label="target", color="#cbd5e1")
    ax.bar(x + w / 2, realized, w, label="realized (CV)", color="#16a34a")
    for xi, v in zip(x + w / 2, realized):
        ax.text(xi, v, f"{v:.0f}%", ha="center", va="bottom", fontsize=8)
    ax.set_xticks(x); ax.set_xticklabels([f"{lvl}%" for lvl, _ in LEVELS])
    ax.set_ylabel("coverage (%)"); ax.set_ylim(0, 105); ax.legend()
    ax.set_title("gradientBoost intervals - realized vs target coverage")
    fig.tight_layout(); fig.savefig(os.path.join(out, "gbmCoverage.png"), dpi=110); plt.close(fig)
    print(f"\nPlots -> {out}")


if __name__ == "__main__":
    raise SystemExit(main())
