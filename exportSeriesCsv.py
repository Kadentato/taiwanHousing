"""Derive a tidy CSV market-price time series from the exported JSON.

One canonical dataset, read many ways: the web app's monthlyMarketSeries.json is
already the per-city/region/national monthly series — this reflows it into a plain
long-format CSV (volume + median unit price, nominal AND real 2021 NT$) that anyone
(students, analysts, the firm) can load in one line. Cheap derivation from the
shipped JSON, not the 2.8 GB DB.

    python exportSeriesCsv.py     # -> webApp/dataFiles/marketSeriesMonthly.csv
"""

from __future__ import annotations

import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
import sys  # noqa: E402
sys.path.insert(0, HERE)
from dataPipeline.inflation import CPI_BASE_YEAR, cpiIndex  # noqa: E402

DATA = os.path.join(HERE, "webApp", "dataFiles")


def main() -> int:
    series = json.load(open(os.path.join(DATA, "monthlyMarketSeries.json"), encoding="utf-8"))
    summary = json.load(open(os.path.join(DATA, "summary.json"), encoding="utf-8"))
    cityName = {c["cityCode"]: c["nameEn"] for c in summary["cities"]}
    regionName = {str(r["regionId"]): r["nameEn"] for r in summary["regions"]}

    def real(nominal, year):
        return None if nominal is None else round(nominal * cpiIndex(CPI_BASE_YEAR) / cpiIndex(year))

    rows = []
    scopes = [("national", "", "Taiwan", series.get("national", {}))]
    scopes += [("region", k, regionName.get(k, k), v) for k, v in series.get("regions", {}).items()]
    scopes += [("city", k, cityName.get(k, k), v) for k, v in series.get("cities", {}).items()]

    for level, key, name, byType in scopes:
        for txn, s in byType.items():
            for ym, n, med in zip(s["months"], s["count"], s["medUnitPrice"]):
                year = int(ym[:4])
                rows.append({"level": level, "key": key, "name": name, "txnType": txn,
                             "year": year, "month": int(ym[5:7]), "ym": ym, "count": n,
                             "unitPriceNominal": med, f"unitPriceReal{CPI_BASE_YEAR}": real(med, year)})

    out = os.path.join(DATA, "marketSeriesMonthly.csv")
    cols = ["level", "key", "name", "txnType", "year", "month", "ym", "count",
            "unitPriceNominal", f"unitPriceReal{CPI_BASE_YEAR}"]
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows):,} rows ({len(scopes)} scopes) -> {out} ({os.path.getsize(out)/1e3:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
