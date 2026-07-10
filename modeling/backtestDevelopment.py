"""Backtest & cross-validate the development valuation (revenue side).

Validates the two pieces the consultant flagged as unchecked — the forward price
projection (+ scenario band) and the new-build premium — on real out-of-time
outcomes, per BUILDING TYPE (towers, low-rise, walk-ups, and houses/透天), not just
towers. (The cross-sectional price model itself is validated by crossValidate.py /
clusterCv.py; here we test the parts layered on top.)

Ground truth = pre-sale (預售) median unit price by (city, type, year) — what new
units actually contracted for.

  PART A  Forward projection, rolling origin: anchor on the ACTUAL pre-sale price in
          base year B, project H years with the tool's method (Base = city growth
          from history<=B; Bull +2%/yr; Bear -4%/yr), compare to the actual price in
          B+H. Reports point error AND whether the Bear..Bull band contained reality.
  PART B  New-build premium, out-of-time: does new-resale × premium (estimated on
          years < T) predict the pre-sale price in year T? Tests premium stability.

    python modeling/backtestDevelopment.py
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

TYPES = ["residentialTower", "elevatorBuildingLowRise", "walkUpApartment", "townhouse"]
LABEL = {"residentialTower": "tower", "elevatorBuildingLowRise": "low-rise+lift",
         "walkUpApartment": "walk-up", "townhouse": "house / 透天"}
MINN = 25
LO, HI = 5000, 3_000_000
BULL_ADD, BEAR = 0.02, -0.04


def load(name):
    df = pd.read_parquet(os.path.join(HERE, "data", name))
    df = df[(df["unitPricePerM2"] >= LO) & (df["unitPricePerM2"] <= HI)
            & df["buildingType"].isin(TYPES) & df["saleYear"].between(2013, 2024)]
    return df


def medDict(df, minN):
    g = df.groupby(["cityCode", "buildingType", "saleYear"])["unitPricePerM2"].agg(["median", "size"])
    g = g[g["size"] >= minN]
    return {idx: float(v) for idx, v in g["median"].items()}


def report(rows, extra_hdr=""):
    d = pd.DataFrame(rows)
    print(f"  {'product':<16}{'n':>5}{'medAPE':>9}{'meanAPE':>9}{extra_hdr}")
    for t in TYPES + ["__all__"]:
        sub = d if t == "__all__" else d[d["type"] == t]
        if sub.empty:
            continue
        name = "OVERALL" if t == "__all__" else LABEL[t]
        line = (f"  {name:<16}{len(sub):>5}{sub['ape'].median()*100:>8.1f}%{sub['ape'].mean()*100:>8.1f}%")
        if "inRange" in sub:
            line += f"{sub['inRange'].mean()*100:>13.0f}%"
        print(line)
    return d


def main() -> int:
    presale = load("presale.parquet")
    sales = load("sales.parquet")
    sales = sales[sales["relatedPartyDeal"].fillna(0) != 1]
    rsNew = sales[sales["buildingAgeYears"] <= 2]

    psd = medDict(presale, MINN)                       # pre-sale median by (city,type,year)
    rsd = medDict(rsNew, MINN)                          # new-resale (age<=2) median
    cityYear = sales.groupby(["cityCode", "saleYear"])["unitPricePerM2"].median()

    def growth(city, upto):
        try:
            s = cityYear.loc[city]
        except KeyError:
            return 0.03
        s = s[(s.index >= 2013) & (s.index <= upto)]
        if len(s) < 3:
            return 0.03
        yoy = np.diff(np.log(s.to_numpy()))
        return float(np.mean(yoy[-5:]))

    cities = sorted({c for (c, _t, _y) in psd})

    # ---- PART A: forward projection + scenario coverage (rolling origin) ----
    rowsA = []
    for B in range(2016, 2022):
        for H in (2, 3):
            T = B + H
            if T > 2024:
                continue
            for city in cities:
                for t in TYPES:
                    if (city, t, B) in psd and (city, t, T) in psd:
                        g = growth(city, B)
                        anchor, actual = psd[(city, t, B)], psd[(city, t, T)]
                        base = anchor * np.exp(g * H)
                        bull = anchor * np.exp((g + BULL_ADD) * H)
                        bear = anchor * np.exp(BEAR * H)
                        rowsA.append({"type": t, "H": H, "ape": abs(base - actual) / actual,
                                      "inRange": bool(bear <= actual <= bull),
                                      "logErr": float(np.log(actual / base))})
    print("PART A — forward projection backtest (rolling origin, pre-sale anchored)\n"
          "        Base point error, and coverage of the Bear..Bull range:")
    A = report(rowsA, extra_hdr="  in Bear..Bull")
    print("    forward error (actual / Base projection), for recalibrating the band:")
    for H in (2, 3):
        s = A[A["H"] == H]
        if not s.empty:
            print(f"      {H}yr: medAPE {s['ape'].median()*100:.1f}%  ·  bias {s['logErr'].mean()*100:+.0f}%"
                  f"  ·  spread ±{s['logErr'].std()*100:.0f}%  ·  per-yr σ≈{s['logErr'].std()/H:.3f}"
                  f"  ·  Bear..Bull cov {s['inRange'].mean()*100:.0f}%")

    # ---- PART B: new-build premium out-of-time ----
    def premForYear(T):
        psW = presale[(presale.saleYear >= T - 3) & (presale.saleYear < T)]
        rsW = rsNew[(rsNew.saleYear >= T - 3) & (rsNew.saleYear < T)]
        overall = (psW["unitPricePerM2"].median() / rsW["unitPricePerM2"].median()) if len(rsW) else 1.0
        out = {}
        for t in TYPES:
            p = psW.loc[psW.buildingType == t, "unitPricePerM2"]
            r = rsW.loc[rsW.buildingType == t, "unitPricePerM2"]
            out[t] = float(p.median() / r.median()) if (len(p) >= 50 and len(r) >= 50 and r.median() > 0) else overall
        return out, float(overall)

    rowsB, premRecent = [], {}
    for T in (2022, 2023, 2024):
        prem, overall = premForYear(T)
        premRecent = prem
        for city in cities:
            for t in TYPES:
                if (city, t, T) in psd and (city, t, T) in rsd:
                    pr = prem.get(t) or overall
                    pred = rsd[(city, t, T)] * pr
                    rowsB.append({"type": t, "ape": abs(pred - psd[(city, t, T)]) / psd[(city, t, T)]})
    print("\nPART B — new-build premium out-of-time (new-resale × premium → pre-sale):")
    report(rowsB)
    print("  premium by product (most recent window): "
          + ", ".join(f"{LABEL[t]} ×{premRecent[t]:.2f}" for t in TYPES if t in premRecent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
