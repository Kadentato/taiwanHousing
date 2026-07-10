"""Cross-validate locally-weighted (adaptive) conformal intervals vs the global band.

Rolling-origin CV across the full history (one test month per year, 2016..2024, so
the flat / boom / cooling regimes are all represented). Each fold: fit the price
model on data before the test month, calibrate one-step-ahead, and compare the
global constant-width band to the adaptive band  mu(x) ± q·sigma(x)  where sigma(x)
is a difficulty model. We check coverage stays on target in EVERY regime, the
typical band tightens, and it holds across building types (houses included).

    python modeling/adaptiveIntervals.py
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import crossValidate as cv  # noqa: E402

LEVELS = [(50, 0.50), (80, 0.20), (95, 0.05)]
TEST_MONTHS = [cv.midx(y, 6) for y in range(2016, 2024)] + [cv.midx(2024, 2)]
TYPES = ["residentialTower", "elevatorBuildingLowRise", "walkUpApartment", "townhouse"]
TLABEL = {"residentialTower": "tower", "elevatorBuildingLowRise": "low-rise+lift",
          "walkUpApartment": "walk-up", "townhouse": "house/透天"}
TRAIN_CAP = 200000
SIGMA_FLOOR = 0.03


def rhoFeatures(sub, enc):
    num = sub[cv.NUMERIC].to_numpy(float)
    cat = np.column_stack([sub[c].map(enc[c][0]).fillna(enc[c][1]).to_numpy(float) for c in cv.CATCOLS])
    return np.hstack([num, cat])


def fitRho(train, absResid):
    enc = {c: cv.targetEncode(train[c], absResid) for c in cv.CATCOLS}
    m = HistGradientBoostingRegressor(max_iter=140, learning_rate=0.1, max_leaf_nodes=31,
                                      min_samples_leaf=200, random_state=0)
    m.fit(rhoFeatures(train, enc), absResid)
    return m, enc


def rhoPredict(me, sub):
    m, enc = me
    return np.clip(m.predict(rhoFeatures(sub, enc)), SIGMA_FLOOR, None)


def conformalQ(scores, alpha):
    s = np.sort(scores)
    n = len(s)
    return float(s[min(int(np.ceil((n + 1) * (1 - alpha))), n) - 1])


def main() -> int:
    df = cv.loadData(os.path.join(HERE, "data", "sales.parquet"))
    df = df[df["relatedPartyDeal"].fillna(0) != 1].copy()

    def cap(d):
        return d.sample(TRAIN_CAP, random_state=0) if len(d) > TRAIN_CAP else d

    rng = np.random.RandomState(0)
    parts = []
    print(f"Rolling-origin CV over {len(TEST_MONTHS)} folds "
          f"({cv.label(TEST_MONTHS[0])}..{cv.label(TEST_MONTHS[-1])}), arm's-length gradient boost ...")

    for T in TEST_MONTHS:
        test = df[df["monthIndex"] == T]
        calib = df[df["monthIndex"] == T - 1]
        if len(test) < 200 or len(calib) < 400:
            continue
        predict = cv.makePredictor("gradientBoost", cap(df[df["monthIndex"] < T]), {})
        predPrev = cv.makePredictor("gradientBoost", cap(df[df["monthIndex"] < T - 1]), {})
        logResid = test["logReal"].to_numpy() - predict(test)
        residC = np.abs(calib["logReal"].to_numpy() - predPrev(calib))

        idx = rng.permutation(len(calib))
        a, b = idx[: len(idx) // 2], idx[len(idx) // 2:]
        rho = fitRho(calib.iloc[a], residC[a])
        scoresB = residC[b] / rhoPredict(rho, calib.iloc[b])
        sigT = rhoPredict(rho, test)

        p = pd.DataFrame({"month": cv.label(T), "type": test["buildingType"].to_numpy(),
                          "absResid": np.abs(logResid), "sigT": sigT})
        for lvl, alpha in LEVELS:
            p[f"qg{lvl}"] = conformalQ(residC, alpha)                    # global width (log)
            p[f"qa{lvl}"] = conformalQ(scoresB, alpha)                   # normalized quantile
        parts.append(p)
        print(f"  scored {cv.label(T)}  (test {len(test):,})")

    pool = pd.concat(parts, ignore_index=True)

    def cov_wid(sub, lvl):
        cg = (sub["absResid"] <= sub[f"qg{lvl}"]).mean()
        wg = (np.exp(sub[f"qg{lvl}"]) - 1).mean()
        half = sub[f"qa{lvl}"] * sub["sigT"]
        ca = (sub["absResid"] <= half).mean()
        wa = np.exp(half) - 1
        return cg, wg, ca, wa

    # ---- pooled: global vs adaptive ----
    print(f"\n=== Pooled ({len(pool):,} test sales) ===")
    print(f"{'level':>6}{'method':>10}{'coverage':>10}{'band ±% (typical)':>20}{'  [easy .. hard]':>18}")
    print("-" * 64)
    for lvl, _ in LEVELS:
        cg, wg, ca, wa = cov_wid(pool, lvl)
        print(f"{lvl:>5}%{'global':>10}{cg*100:>9.1f}%{wg*100:>17.0f}%")
        print(f"{'':>6}{'adaptive':>10}{ca*100:>9.1f}%{np.median(wa)*100:>17.0f}%"
              f"   ±{np.percentile(wa,10)*100:.0f}% .. ±{np.percentile(wa,90)*100:.0f}%")

    # ---- per-fold coverage stability (80%) ----
    print("\n=== 80% coverage by fold (regime robustness) ===")
    print(f"  {'fold':>9}{'global':>9}{'adaptive':>10}{'adapt med ±%':>14}")
    for m, sub in pool.groupby("month", sort=False):
        cg, _wg, ca, wa = cov_wid(sub, 80)
        print(f"  {m:>9}{cg*100:>8.1f}%{ca*100:>9.1f}%{np.median(wa)*100:>13.0f}%")

    # ---- per building type (80%) ----
    print("\n=== 80% by building type (pooled) ===")
    print(f"  {'product':<16}{'n':>7}{'adapt cov':>11}{'adapt med ±%':>14}{'vs global ±%':>14}")
    for t in TYPES:
        sub = pool[pool["type"] == t]
        if len(sub) < 500:
            continue
        cg, wg, ca, wa = cov_wid(sub, 80)
        print(f"  {TLABEL[t]:<16}{len(sub):>7,}{ca*100:>10.1f}%{np.median(wa)*100:>13.0f}%{wg*100:>13.0f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
