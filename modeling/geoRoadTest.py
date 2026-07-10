"""Does an offline "geocode" (road/段 pulled from the address) tighten the band?

The LVR has no coordinates, but the address text does carry sub-district location.
buildDataset now extracts a `roadKey` (districtEn|road). Here we test, out-of-time,
whether adding it as a target-encoded feature to the gradient boost improves the
INDIVIDUAL prediction (lower typical error / tighter band) vs the current features —
and report it honestly (incl. what share of rows actually resolved to a road).

    python modeling/geoRoadTest.py
"""

from __future__ import annotations

import os
import sys

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import crossValidate as cv  # noqa: E402

BASE_COLS = cv.CATCOLS
CAP = 300000


def feats(sub, encs, cols):
    num = sub[cv.NUMERIC].to_numpy(float)
    cat = np.column_stack([sub[c].map(encs[c][0]).fillna(encs[c][1]).to_numpy(float) for c in cols])
    return np.hstack([num, cat])


def fit(train, cols):
    y = train["logReal"].to_numpy()
    encs = {c: cv.targetEncode(train[c], y) for c in cols}
    m = HistGradientBoostingRegressor(max_iter=250, learning_rate=0.08, max_leaf_nodes=31,
                                      min_samples_leaf=120, l2_regularization=1.0, early_stopping=True,
                                      n_iter_no_change=20, validation_fraction=0.1, random_state=0)
    m.fit(feats(train, encs, cols), y)
    return m, encs


def evaluate(df, months, cols):
    ape, resid = [], []
    for T in months:
        tr = df[df["monthIndex"] < T]
        tr = tr.sample(CAP, random_state=0) if len(tr) > CAP else tr
        te = df[df["monthIndex"] == T]
        m, encs = fit(tr, cols)
        p = m.predict(feats(te, encs, cols))
        actual = te["logReal"].to_numpy()
        resid.extend(actual - p)
        aVal = np.exp(actual)
        ape.extend(np.abs(np.exp(p) - aVal) / aVal)
    ape = np.asarray(ape); resid = np.asarray(resid)
    band80 = float(np.exp(np.quantile(np.abs(resid), 0.80)) - 1)     # width giving ~80% coverage
    return {"medAPE": float(np.median(ape)), "cov10": float((ape <= .10).mean()),
            "cov20": float((ape <= .20).mean()), "band80": band80, "n": len(ape)}


def main() -> int:
    df = cv.loadData(os.path.join(HERE, "data", "sales.parquet"))
    df = df[df["relatedPartyDeal"].fillna(0) != 1].copy()
    if "roadKey" not in df.columns:
        print("roadKey column missing — rebuild sales.parquet first.")
        return 1
    hasRoad = (df["roadKey"] != df["districtEn"]).mean()
    nRoads = df["roadKey"].nunique()
    asOf = int(df["monthIndex"].max())
    months = [asOf - 2, asOf - 1, asOf]
    print(f"Rows resolved to a road: {hasRoad*100:.0f}%  ·  distinct roadKeys: {nRoads:,}  ·  "
          f"distinct districts: {df['districtEn'].nunique()}")
    print(f"Out-of-time test on {', '.join(cv.label(m) for m in months)} (arm's-length gradient boost)\n")

    base = evaluate(df, months, BASE_COLS)
    road = evaluate(df, months, BASE_COLS + ["roadKey"])
    print(f"{'variant':<14}{'medAPE':>9}{'<=10%':>9}{'<=20%':>9}{'80% band ±%':>14}")
    print("-" * 55)
    for name, r in [("baseline", base), ("+ roadKey", road)]:
        print(f"{name:<14}{r['medAPE']*100:>8.1f}%{r['cov10']*100:>8.1f}%{r['cov20']*100:>8.1f}%{r['band80']*100:>13.0f}%")
    d = (base["medAPE"] - road["medAPE"]) / base["medAPE"] * 100
    print(f"\nroadKey change: medAPE {d:+.1f}%, 80% band "
          f"{base['band80']*100:.0f}% -> {road['band80']*100:.0f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
