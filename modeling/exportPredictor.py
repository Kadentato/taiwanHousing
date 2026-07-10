"""Export the gradient-boosted price model to a compact JSON the web app runs
client-side, so the browser predictor gives a point price + calibrated 50/80/95%
range with no backend.

Serialises the HistGradientBoosting trees (all features numeric — location is
target-encoded — so no categorical bitsets), the target-encoding maps, imputation
defaults, conformal band quantiles (calibrated one-step-ahead), and the CPI factor
that converts the model's real 2021 NT$ back to nominal. A numeric self-check
compares the exported tree-walk against sklearn's own predict() before writing.

    python modeling/exportPredictor.py     # -> webApp/dataFiles/predictor.json
"""

from __future__ import annotations

import datetime
import json
import os
import sys

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import crossValidate as cv  # noqa: E402
from dataPipeline import valueMappings as vm  # noqa: E402
from dataPipeline.inflation import CPI_BASE_YEAR, cpiIndex  # noqa: E402

LEVELS = [(50, 0.50), (80, 0.20), (95, 0.05)]
CAP = 400000
OUT = os.path.join(os.path.dirname(HERE), "webApp", "dataFiles", "predictor.json")


def fitBrowserGBM(train):
    """Slightly smaller trees than the CV model so the JSON stays light for the browser."""
    y = train["logReal"].to_numpy()
    enc = {c: cv.targetEncode(train[c], y) for c in cv.CATCOLS}
    model = HistGradientBoostingRegressor(
        max_iter=250, learning_rate=0.08, max_leaf_nodes=31, min_samples_leaf=120,
        l2_regularization=1.0, early_stopping=True, n_iter_no_change=20,
        validation_fraction=0.1, random_state=0)
    model.fit(cv.gbmFeatures(train, enc), y)
    return model, enc


def serialiseTrees(model):
    """Each tree -> list of nodes. Leaf: [1, value]. Split: [0, feat, thr, left, right, missLeft]."""
    trees = []
    for group in model._predictors:
        nodes = group[0].nodes
        names = nodes.dtype.names
        thrKey = "num_threshold" if "num_threshold" in names else "threshold"
        tree = []
        for nd in nodes:
            if int(nd["is_leaf"]):
                tree.append([1, round(float(nd["value"]), 7)])
            else:
                tree.append([0, int(nd["feature_idx"]), float(nd[thrKey]),
                             int(nd["left"]), int(nd["right"]), int(nd["missing_go_to_left"])])
        trees.append(tree)
    return trees, float(np.ravel(model._baseline_prediction)[0])


def walk(trees, baseline, X):
    """Reference implementation of the JS tree-walk, for the self-check."""
    out = np.full(len(X), baseline)
    for tree in trees:
        for i in range(len(X)):
            n = 0
            while True:
                nd = tree[n]
                if nd[0] == 1:
                    out[i] += nd[1]
                    break
                x = X[i, nd[1]]
                if np.isnan(x):
                    n = nd[3] if nd[5] else nd[4]
                else:
                    n = nd[3] if x <= nd[2] else nd[4]
    return out


def main() -> int:
    df = cv.loadData(os.path.join(HERE, "data", "sales.parquet"))
    # Train + calibrate on arm's-length market deals only: special-relationship
    # transfers (gifts, family sales) are priced off-market and fatten the residual
    # spread, so dropping them tightens the intervals without hurting normal-sale
    # accuracy. This directly narrows the conformal band width.
    before = len(df)
    df = df[df["relatedPartyDeal"].fillna(0) != 1].reset_index(drop=True)
    print(f"Dropped {before - len(df):,} related-party deals; {len(df):,} arm's-length sales remain.")
    asOf = int(df["monthIndex"].max())
    asOfYear = cv.BASE_YEAR + asOf // 12

    def cap(d):
        return d.sample(CAP, random_state=0) if len(d) > CAP else d

    print(f"Fitting browser GBM on <= {CAP:,} of {len(df):,} sales through {cv.label(asOf)} ...")
    train = cap(df)
    model, enc = fitBrowserGBM(train)
    trees, baseline = serialiseTrees(model)

    # self-check: exported tree-walk vs sklearn on a sample
    chk = cap(df).sample(2000, random_state=1)
    Xchk = cv.gbmFeatures(chk, enc)
    diff = float(np.max(np.abs(walk(trees, baseline, Xchk) - model.predict(Xchk))))
    print(f"  self-check max |exported - sklearn| = {diff:.2e}  ({len(trees)} trees)")
    assert diff < 1e-5, "exported tree-walk disagrees with sklearn"

    # conformal band: calibrate one-step-ahead on the most recent 3 months
    calStart = asOf - 2
    calib = df[(df["monthIndex"] >= calStart) & (df["monthIndex"] <= asOf)]
    mPrev, encPrev = fitBrowserGBM(cap(df[df["monthIndex"] < calStart]))
    resid = calib["logReal"].to_numpy() - mPrev.predict(cv.gbmFeatures(calib, encPrev))
    q = {lvl: cv.conformalQ(resid, alpha) for lvl, alpha in LEVELS}
    print("  global band: " + ", ".join(f"{lvl}% +-{(np.exp(q[lvl])-1)*100:.0f}%" for lvl, _ in LEVELS))

    # ---- adaptive (locally-weighted) conformal: a difficulty model sigma(x) scales
    # the band per property, so standard homes get a tighter band and oddballs a wider
    # one. Split calib: A fits sigma, B calibrates the normalized quantile.
    absR = np.abs(resid)
    ridx = np.random.RandomState(1).permutation(len(calib))
    A, B = ridx[: len(ridx) // 2], ridx[len(ridx) // 2:]
    calA = calib.iloc[A]
    encSig = {c: cv.targetEncode(calA[c], absR[A]) for c in cv.CATCOLS}
    sig = HistGradientBoostingRegressor(max_iter=140, learning_rate=0.1, max_leaf_nodes=31,
                                        min_samples_leaf=200, random_state=0)
    sig.fit(cv.gbmFeatures(calA, encSig), absR[A])
    sigTrees, sigBase = serialiseTrees(sig)
    SIG_FLOOR = 0.03
    sB = np.clip(sig.predict(cv.gbmFeatures(calib.iloc[B], encSig)), SIG_FLOOR, None)
    qa = {lvl: cv.conformalQ(absR[B] / sB, alpha) for lvl, alpha in LEVELS}
    Xs = cv.gbmFeatures(chk, encSig)
    diffS = float(np.max(np.abs(walk(sigTrees, sigBase, Xs) - sig.predict(Xs))))
    assert diffS < 1e-5, "sigma tree-walk disagrees with sklearn"
    med = float(np.median(sB))
    print(f"  sigma self-check {diffS:.1e}; adaptive typical band: "
          + ", ".join(f"{lvl}% +-{(np.exp(qa[lvl]*med)-1)*100:.0f}%" for lvl, _ in LEVELS))

    # UI option lists from the data
    cityCodes = sorted(df["cityCode"].unique())
    districtsByCity = {c: sorted(df.loc[df["cityCode"] == c, "districtEn"].unique().tolist())
                       for c in cityCodes}
    payload = {
        # provenance stamp — so a decision made with this model is traceable to the
        # exact data vintage + build date it came from.
        "version": {"builtOn": datetime.date.today().isoformat(), "dataThrough": cv.label(asOf),
                    "trainRows": int(len(train)), "model": "gradientBoost + locally-weighted conformal"},
        "asOfLabel": cv.label(asOf), "asOfMonthIndex": asOf,
        "nominalFactor": cpiIndex(asOfYear) / cpiIndex(CPI_BASE_YEAR),
        "priceLo": cv.PRICE_LO, "priceHi": cv.PRICE_HI, "m2PerPing": cv.M2_PER_PING,
        "numericOrder": cv.NUMERIC, "catOrder": cv.CATCOLS,
        "baseline": baseline, "trees": trees,
        "encoders": {c: {"map": {str(k): round(float(v), 6) for k, v in enc[c][0].items()},
                         "global": round(float(enc[c][1]), 6)} for c in cv.CATCOLS},
        "defaults": {col: round(float(df[col].median()), 4) for col in
                     ["livingAreaPing", "landAreaPing", "mainBuildingRatio", "buildingAgeYears",
                      "bedrooms", "livingRooms", "bathrooms", "transferFloor", "totalFloors"]},
        "catDefaults": {c: df[c].mode().iloc[0] for c in cv.CATCOLS[2:]},
        "levels": {str(lvl): round(float(q[lvl]), 6) for lvl, _ in LEVELS},
        "sigma": {
            "baseline": sigBase, "trees": sigTrees, "floor": SIG_FLOOR,
            "encoders": {c: {"map": {str(k): round(float(v), 6) for k, v in encSig[c][0].items()},
                             "global": round(float(encSig[c][1]), 6)} for c in cv.CATCOLS},
            "levels": {str(lvl): round(float(qa[lvl]), 6) for lvl, _ in LEVELS},
        },
        "ui": {
            "cities": [{"code": c, "name": vm.CITY_BY_CODE.get(c, (c,))[0]} for c in cityCodes],
            "districtsByCity": districtsByCity,
            "buildingTypes": sorted(df["buildingType"].unique().tolist()),
            "mainUses": sorted(df["mainUse"].unique().tolist()),
            "mainMaterials": sorted(df["mainMaterial"].unique().tolist()),
        },
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"Wrote {OUT}  ({os.path.getsize(OUT)/1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
