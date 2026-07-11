"""Render the README hero image: a filled map of Taiwan shaded by each district's
median housing sale price. Built entirely from the shipped web-data files (township
polygons + per-district aggregates) — no database needed.

    python spatialAnalysis/makeDistrictMap.py
"""
from __future__ import annotations

import os

import matplotlib
matplotlib.use("Agg")
import geopandas as gpd  # noqa: E402
import numpy as np  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "districtMedianUnitPrice.png")


def main() -> int:
    polys = gpd.read_file(os.path.join(ROOT, "geoReference", "townshipBoundaries.geojson"))
    agg = gpd.read_file(os.path.join(ROOT, "webApp", "dataFiles", "districtAggregates.geojson"))
    price = {(r.cityCode, r.districtZh): r.saleMedUnitPrice for r in agg.itertuples()
             if r.saleMedUnitPrice is not None}
    polys["price"] = [price.get((r.cityCode, r.town)) for r in polys.itertuples()]
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
