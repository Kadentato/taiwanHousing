"""Value a NEW-BUILD development for a Taiwan developer (revenue side only).

Reuses the validated gradient-boosted price model (no new model) and reframes it
for a build decision. Consultant-review upgrades folded in:

  * NEW-BUILD PREMIUM from pre-sale data — the model is calibrated on resales, so
    we scale its age-0 output by the market premium of pre-sale (預售) prices over
    newly-completed resales (age<=2), measured per city from presale.parquet.
  * SALEABLE-AREA (公設) BASIS — revenue is priced on the saleable/deed area (含公設);
    new towers carry ~33% 公設, so we set a realistic per-type main-building ratio
    (override with --gongshe) rather than assuming a resale mix.
  * SCENARIO / STRESS forward — instead of one growth number, report Bull / Base /
    Bear(stress) price paths to the sale year, and underwrite to the Bear downside.

Values a DEVELOPMENT (N units) so per-home noise averages out; the residual risk is
the district-cohort model error (measured out-of-time), which does NOT diversify.
Revenue only — subtract your land + build + soft costs for margin.

    python modeling/developmentValuation.py --city "Taipei City" --district "Da'an District" \
        --type residentialTower --size 100 --units 40 --year 2028
    python modeling/developmentValuation.py --city "Taichung City" --sweep district
    python modeling/developmentValuation.py --city "Taipei City" --sweep type
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import crossValidate as cv  # noqa: E402
from dataPipeline.inflation import CPI_BASE_YEAR, cpiIndex  # noqa: E402
from dataPipeline.valueMappings import CITY_BY_CODE  # noqa: E402

Z80 = 1.2816                         # P10/P90 z-score for the 80% central band
BULL_ADD = 0.02                      # Bull scenario: ~+2%/yr above trend (log)
BEAR_LOG = -0.04                     # Bear/stress scenario: ~-4%/yr downturn (absolute)
FWD_VOL_PER_YR = 0.14                # forward price log-vol per sqrt(year), calibrated on
                                     # backtestDevelopment.py (out-of-time, 2016-2024)
ALL_RES = ["residentialTower", "elevatorBuildingLowRise", "walkUpApartment", "townhouse"]
EN2CODE = {en: c for c, (en, _z, _r) in CITY_BY_CODE.items()}
# new-build product presets: floors, elevator, and main-building ratio (1 - 公設 - 附屬)
PRODUCT = {
    "residentialTower":        dict(totalFloors=14, elevator=1, mainRatio=0.60),   # ~33% 公設
    "elevatorBuildingLowRise": dict(totalFloors=8,  elevator=1, mainRatio=0.66),   # ~28% 公設
    "walkUpApartment":         dict(totalFloors=5,  elevator=0, mainRatio=0.80),   # ~15% 公設
    "townhouse":               dict(totalFloors=3,  elevator=0, mainRatio=0.85),   # own building
}


def layout(sizeM2):
    if sizeM2 < 50:   return 1, 1, 1
    if sizeM2 < 80:   return 2, 2, 1
    if sizeM2 < 120:  return 3, 2, 2
    return 4, 2, 3


def unitRow(code, districtEn, btype, sizeM2, monthIndex, landShare, mainRatio):
    p = PRODUCT.get(btype, PRODUCT["residentialTower"])
    beds, livings, baths = layout(sizeM2)
    ping = sizeM2 / cv.M2_PER_PING
    floor = max(1, round(p["totalFloors"] / 2))
    return {
        "cityCode": code, "districtEn": districtEn, "buildingType": btype,
        "mainUse": "residential", "mainMaterial": "reinforcedConcrete",
        "livingAreaPing": ping, "landAreaPing": landShare, "mainBuildingRatio": mainRatio,
        "buildingAgeYears": 0.0, "ageSq": 0.0,
        "bedrooms": beds, "livingRooms": livings, "bathrooms": baths,
        "transferFloor": floor, "totalFloors": p["totalFloors"], "floorRatio": floor / p["totalFloors"],
        "hasParking": 1, "hasElevator": p["elevator"], "hasManagementOrg": 1, "hasCompartments": 1,
        "logArea": np.log(max(ping, 1.0)), "logLand": float(np.log1p(max(landShare, 0.0))),
        "monthIndex": monthIndex,
    }


def cityGrowth(df, code):
    """Nominal annual median unit price -> annual log-growth (recent 5yr trend)."""
    med = df[df["cityCode"] == code].groupby("saleYear")["unitPricePerM2"].median()
    med = med[med.index >= 2015]
    if len(med) < 4:
        med = df.groupby("saleYear")["unitPricePerM2"].median()
        med = med[med.index >= 2015]
    yoy = np.diff(np.log(med.to_numpy()))
    return float(np.mean(yoy[-5:] if len(yoy) >= 5 else yoy))


def crossSectionalError(df, model, holdout):
    """District-cohort model error out-of-time: std of log(predMedian/actualMedian)."""
    test = df[df["monthIndex"] == holdout].copy()
    test["pred"] = cv.clipExp(model(test))
    g = test.groupby("districtEn").agg(n=("realUnit", "size"),
                                       am=("realUnit", "median"), pm=("pred", "median"))
    g = g[g["n"] >= 20]
    return float(np.std(np.log(g["pm"] / g["am"])))


def _ratio(p, r, minN):
    return float(p.median() / r.median()) if (len(p) >= minN and len(r) >= minN and r.median() > 0) else None


def newBuildPremium(saleDf, presaleDf):
    """Premium of pre-sale (new) over newly-completed resale (age<=2), per (city,type)
    with fallback to per-type then overall — so houses (透天) get their OWN premium,
    not the tower one. Returns (byCityType, byType, overall)."""
    if presaleDf is None or presaleDf.empty:
        return {}, {}, 1.0
    ps = presaleDf[(presaleDf["buildingType"].isin(ALL_RES)) & (presaleDf["saleYear"] >= 2020)]
    rs = saleDf[(saleDf["buildingType"].isin(ALL_RES)) & (saleDf["saleYear"] >= 2020)
                & (saleDf["buildingAgeYears"] <= 2)]
    overall = _ratio(ps["unitPricePerM2"], rs["unitPricePerM2"], 1) or 1.0
    byType, byCityType = {}, {}
    for t in ALL_RES:
        byType[t] = _ratio(ps.loc[ps.buildingType == t, "unitPricePerM2"],
                           rs.loc[rs.buildingType == t, "unitPricePerM2"], 50) or overall
    for code in saleDf["cityCode"].unique():
        for t in ALL_RES:
            r = _ratio(ps[(ps.cityCode == code) & (ps.buildingType == t)]["unitPricePerM2"],
                       rs[(rs.cityCode == code) & (rs.buildingType == t)]["unitPricePerM2"], 30)
            byCityType[(code, t)] = r if r is not None else byType[t]
    return byCityType, byType, overall


def absorptionStats(presaleDf, years=(2021, 2022, 2023, 2024)):
    """Avg new units PRE-SOLD per year by (city, district, type) — a demand-depth /
    absorption proxy. LVR has completed transactions, not live listings, so this is
    market *throughput* of new units, not a sold/available rate."""
    if presaleDf is None or presaleDf.empty:
        return {}
    ps = presaleDf[presaleDf["saleYear"].isin(years)]
    return (ps.groupby(["cityCode", "districtEn", "buildingType"]).size() / len(years)).to_dict()


def valueDevelopment(model, code, districtEn, btype, sizeM2, units, year, asOf,
                     sigmaX, landByType, gLog, premium, gongshe):
    p = PRODUCT.get(btype, PRODUCT["residentialTower"])
    mainRatio = min(max(1 - gongshe - 0.05, 0.15), 1.0) if gongshe is not None else p["mainRatio"]
    row = unitRow(code, districtEn, btype, sizeM2, asOf, float(landByType.get(btype, 0.0)), mainRatio)
    baseReal = float(cv.clipExp(model(pd.DataFrame([row])))[0])
    asOfYear = cv.BASE_YEAR + asOf // 12
    baseNominal = baseReal * cpiIndex(asOfYear) / cpiIndex(CPI_BASE_YEAR)
    newLevel = baseNominal * premium                  # resale-model -> new-build (presale) level
    horizon = max(0, year - asOfYear)
    area = sizeM2 * units

    def scen(g):
        unit = newLevel * np.exp(g * horizon)
        return unit, unit * area

    scenarios = {"Bull": scen(gLog + BULL_ADD), "Base": scen(gLog), "Bear (stress)": scen(BEAR_LOG)}
    growth = {"Bull": gLog + BULL_ADD, "Base": gLog, "Bear (stress)": BEAR_LOG}
    baseUnit, baseTotal = scenarios["Base"]
    # 80% band, calibrated: cross-sectional model error + backtest forward risk (~sqrt-time)
    sigmaF = FWD_VOL_PER_YR * np.sqrt(horizon)
    sigma = float(np.sqrt(sigmaX ** 2 + sigmaF ** 2))
    lo, hi = baseTotal * np.exp(-Z80 * sigma), baseTotal * np.exp(Z80 * sigma)
    return dict(baseNominal=baseNominal, premium=premium, newLevel=newLevel, mainRatio=mainRatio,
                horizon=horizon, scenarios=scenarios, growth=growth, baseUnit=baseUnit, baseTotal=baseTotal,
                lo=lo, hi=hi, underwrite=lo, sigmaX=sigmaX, sigmaF=sigmaF, sigma=sigma)


def _m(v):
    return f"NT${v/1e6:,.1f}M"


def main() -> int:
    ap = argparse.ArgumentParser(description="Value a new-build development (revenue only).")
    ap.add_argument("--city", default="Taipei City")
    ap.add_argument("--district", default=None)
    ap.add_argument("--type", default="residentialTower", choices=list(PRODUCT))
    ap.add_argument("--size", type=float, default=100.0, help="saleable (含公設) unit size, m²")
    ap.add_argument("--units", type=int, default=40)
    ap.add_argument("--year", type=int, default=None, help="target sale year")
    ap.add_argument("--growth", type=float, default=None, help="override Base annual %% growth")
    ap.add_argument("--gongshe", type=float, default=None, help="override 公設 ratio (e.g. 0.33)")
    ap.add_argument("--sweep", choices=["type", "district"], default=None)
    args = ap.parse_args()

    code = EN2CODE.get(args.city, args.city if args.city in CITY_BY_CODE else None)
    if code is None:
        ap.error(f"unknown city '{args.city}'")

    df = cv.loadData(os.path.join(HERE, "data", "sales.parquet"))
    df = df[df["relatedPartyDeal"].fillna(0) != 1].copy()
    presalePath = os.path.join(HERE, "data", "presale.parquet")
    presale = None
    if os.path.exists(presalePath):
        presale = pd.read_parquet(presalePath)
        presale = presale[(presale["unitPricePerM2"] >= 5000) & (presale["unitPricePerM2"] <= 3_000_000)]

    holdout = int(df["monthIndex"].max())          # latest COMPLETE month (loadData trims the lagging tail)
    train = df[df["monthIndex"] < holdout]
    if len(train) > 400000:
        train = train.sample(400000, random_state=0)
    asOf = holdout - 1
    asOfYear = cv.BASE_YEAR + asOf // 12
    year = args.year or (asOfYear + 3)
    gOverride = None if args.growth is None else np.log1p(args.growth / 100.0)

    print(f"Training gradient-boost on {len(train):,} arm's-length sales (through {cv.label(asOf)}) ...")
    model = cv.makePredictor("gradientBoost", train, cats={})
    sigmaX = crossSectionalError(df, model, holdout)
    landByType = df.groupby("buildingType")["landAreaPing"].median().to_dict()
    premCityType, premType, premOverall = newBuildPremium(df, presale)

    def prem(t):
        return premCityType.get((code, t)) or premType.get(t) or premOverall

    gLog = gOverride if gOverride is not None else cityGrowth(df, code)

    districts = sorted(df.loc[df["cityCode"] == code, "districtEn"].unique())
    district = args.district or (districts[0] if districts else "unknown")
    cityEn = CITY_BY_CODE[code][0]
    pTag = "override" if gOverride is not None else f"{cityEn} history"

    print(f"\nBase {cv.label(asOf)} (nominal {asOfYear} NT$) · sale {year} (+{year-asOfYear}yr) · {cityEn}")
    print(f"New-build premium ({args.type}): ×{prem(args.type):.2f} (presale vs new resale)"
          + ("" if presale is not None else "  [presale.parquet missing -> 1.00]")
          + f"  ·  Base growth {(np.exp(gLog)-1)*100:+.1f}%/yr ({pTag})  ·  district-cohort error ±{sigmaX*100:.1f}%\n")

    def value(dist, btype):
        return valueDevelopment(model, code, dist, btype, args.size, args.units, year,
                                asOf, sigmaX, landByType, gLog, prem(btype), args.gongshe)

    absorb = absorptionStats(presale)

    def absorbFor(dist, btype):
        return float(absorb.get((code, dist, btype), 0.0))

    def sellout(a):
        return (f"{args.units/a:.1f}y" + ("!" if args.units > 1.5 * a else "")) if a >= 0.5 else "thin"

    if args.sweep == "type":
        print(f"Product sweep — {cityEn}/{district}, {args.units}× {args.size:.0f} m² saleable, sold {year}"
              f"  (sellout '!' = project > 1.5× throughput):")
        print(f"  {'building type':<26}{'$/m² Base':>12}{'total(Base)':>14}{'P10 down':>12}{'new/yr':>8}{'sellout':>9}")
        for t in sorted(PRODUCT, key=lambda t: -value(district, t)["baseTotal"]):
            r, a = value(district, t), absorbFor(district, t)
            print(f"  {t:<26}{r['baseUnit']:>12,.0f}{_m(r['baseTotal']):>14}{_m(r['underwrite']):>12}{a:>8.0f}{sellout(a):>9}")
    elif args.sweep == "district":
        print(f"District sweep — {cityEn}, {args.units}× {args.size:.0f} m² {args.type}, sold {year}"
              f"  (sellout '!' = project > 1.5× throughput):")
        print(f"  {'district':<24}{'$/m² Base':>12}{'total(Base)':>14}{'P10 down':>12}{'new/yr':>8}{'sellout':>9}")
        for d in sorted(districts, key=lambda d: -value(d, args.type)["baseTotal"])[:15]:
            r, a = value(d, args.type), absorbFor(d, args.type)
            print(f"  {d:<24}{r['baseUnit']:>12,.0f}{_m(r['baseTotal']):>14}{_m(r['underwrite']):>12}{a:>8.0f}{sellout(a):>9}")
    else:
        r = value(district, args.type)
        beds, _, baths = layout(args.size)
        gongshePct = (1 - r["mainRatio"] - 0.05) * 100
        print(f"Development: {args.units}× new {args.type} · {args.size:.0f} m² saleable "
              f"(含公設 ~{gongshePct:.0f}%, {beds}BR/{baths}BA) · {cityEn}/{district}")
        print(f"  model base (age 0, resale-calibrated):  {r['baseNominal']:>11,.0f} NT$/m²")
        print(f"  × new-build premium (presale):          ×{r['premium']:>10.2f}")
        print(f"  = new-build price level ({asOfYear}):       {r['newLevel']:>11,.0f} NT$/m²")
        print(f"  {'-'*58}")
        print(f"  Scenarios — growth/yr, projected $/m² and total revenue at {year}:")
        for name in ("Bull", "Base", "Bear (stress)"):
            u, t = r["scenarios"][name]
            print(f"    {name:<15}{(np.exp(r['growth'][name])-1)*100:>+6.1f}%/yr{u:>12,.0f} NT$/m²{_m(t):>15}")
        print(f"  {'-'*58}")
        print(f"  80% range (calibrated: cross-sec ±{r['sigmaX']*100:.0f}% + {r['horizon']}yr forward "
              f"±{r['sigmaF']*100:.0f}%):  {_m(r['lo'])} – {_m(r['hi'])}")
        print(f"  P10 underwriting downside (backtest-calibrated): {_m(r['underwrite'])}  <-- size to this")
        print(f"  {'-'*58}")
        a = absorbFor(district, args.type)
        if a < 0.5:
            print(f"  Absorption: THIN — few comparable new {args.type} pre-sold in {district} lately; demand unproven")
        else:
            note = "  [!] project > 1.5x annual throughput — supply/price risk" if args.units > 1.5 * a else ""
            print(f"  Absorption: ~{a:.0f} new {args.type} pre-sold/yr in {district} -> ~{args.units/a:.1f} yr "
                  f"to sell {args.units} units{note}")
        print(f"  (Revenue on saleable area — subtract land + build + soft costs for margin.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
