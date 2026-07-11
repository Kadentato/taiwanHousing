"""Render the README hero image: a filled map of Taiwan shaded by each district's
median housing sale price. Built entirely from the shipped web-data files (township
polygons + per-district aggregates) — no database needed.

    python spatialAnalysis/makeDistrictMap.py
"""
from __future__ import annotations

import os
import re
from collections import Counter

import matplotlib
matplotlib.use("Agg")
import geopandas as gpd  # noqa: E402
import numpy as np  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "taiwanPriceMap.png")


def main() -> int:
    polys = gpd.read_file(os.path.join(ROOT, "geoReference", "townshipBoundaries.geojson"))
    agg = gpd.read_file(os.path.join(ROOT, "webApp", "dataFiles", "districtAggregates.geojson"))
    # Normalise names before joining: the bundled boundary file predates Taoyuan's 2014 upgrade to a
    # municipality, so it still uses old 鄉/鎮/市 suffixes (中壢市 vs 中壢區), mixes the 臺/台 variant,
    # and adds notes like 鼓山區(海). Strip the admin suffix + any "(…)" note and unify 臺→台.
    def norm(s):
        s = re.sub(r"\(.*?\)", "", str(s)).replace("臺", "台")
        return re.sub(r"[鄉鎮市區]$", "", s).strip()

    price = {(r.cityCode, norm(r.districtZh)): r.saleMedUnitPrice for r in agg.itertuples()
             if r.saleMedUnitPrice is not None}
    # Provincial cities (Hsinchu City, Chiayi City) are recorded at the city level — a single price
    # row, not per-district — so fall back to that one price for all of their district polygons.
    nRows = Counter(r.cityCode for r in agg.itertuples() if r.saleMedUnitPrice is not None)
    cityFallback = {r.cityCode: r.saleMedUnitPrice for r in agg.itertuples()
                    if r.saleMedUnitPrice is not None and nRows[r.cityCode] == 1}
    polys["price"] = [price.get((r.cityCode, norm(r.town)), cityFallback.get(r.cityCode))
                      for r in polys.itertuples()]
    print(f"matched {polys['price'].notna().sum()}/{len(polys)} districts to a median price")

    vals = polys["price"].dropna()
    vmin, vmax = float(np.percentile(vals, 8)), float(np.percentile(vals, 92))

    fig, ax = plt.subplots(figsize=(6.4, 8.4))
    fig.patch.set_facecolor("white")
    # districts with no sales -> light grey, so the full Taiwan landmass still reads as a map
    polys[polys["price"].isna()].plot(ax=ax, color="#eceff3", edgecolor="white", linewidth=0.25)
    polys[polys["price"].notna()].plot(
        ax=ax, column="price", cmap="YlOrRd", vmin=vmin, vmax=vmax,
        edgecolor="white", linewidth=0.25, legend=True,
        legend_kwds={"label": "Median sale price (NT$/m²)", "shrink": 0.42,
                     "format": FuncFormatter(lambda x, _: f"{x / 1000:.0f}k")})
    # frame the main island + Penghu (crops the far-west Kinmen / far-north Matsu specks)
    ax.set_xlim(119.3, 122.05)
    ax.set_ylim(21.85, 25.35)
    ax.set_axis_off()
    ax.set_title("Taiwan — median housing sale price by district", fontsize=13, pad=6)
    fig.tight_layout()
    fig.savefig(OUT, dpi=150, bbox_inches="tight", facecolor="white")
    print(f"wrote {OUT} ({os.path.getsize(OUT) / 1e3:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
