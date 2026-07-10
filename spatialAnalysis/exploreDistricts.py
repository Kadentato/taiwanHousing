"""Demo: load the database into a geopandas GeoDataFrame and do spatial stats.

Shows the intended workflow — the SQLite WKT geometry round-trips straight into
geopandas, so you can compute per-district medians, reproject to a metric CRS,
and plot. Run after ``buildDatabase.py``:

    python spatialAnalysis/exploreDistricts.py

Writes ``spatialAnalysis/districtMedianUnitPrice.png`` and prints the priciest
and cheapest districts for residential sales.
"""

from __future__ import annotations

import os
import sqlite3
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dataPipeline.spatialBuilder import loadHousesGeoDataFrame  # noqa: E402
from dataPipeline.valueMappings import HOUSING_TARGETS  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "database", "taiwanHousing.sqlite")
OUT_PNG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "districtMedianUnitPrice.png")
TWD97 = 3826


def main() -> int:
    if not os.path.exists(DB):
        print("Database not found. Run `python buildDatabase.py` first.", file=sys.stderr)
        return 1

    conn = sqlite3.connect(DB)
    districtName = {r[0]: r[1] for r in conn.execute("SELECT districtId, nameZh FROM districts")}
    cityName = {r[0]: r[1] for r in conn.execute("SELECT cityId, nameEn FROM cities")}

    gdf = loadHousesGeoDataFrame(conn, "transactionType='sale'")
    gdf = gdf[gdf["targetType"].isin(HOUSING_TARGETS) & gdf["unitPricePerM2"].notna()]
    conn.close()
    print(f"Loaded {len(gdf)} residential-sale points into a GeoDataFrame (CRS {gdf.crs}).")

    # --- per-district medians, keeping one representative centroid geometry ---
    grouped = gdf.dissolve(
        by="districtId",
        aggfunc={"unitPricePerM2": "median", "houseId": "count"},
    ).rename(columns={"unitPricePerM2": "medUnitPrice", "houseId": "n"})
    grouped["geometry"] = grouped.geometry.centroid  # collapse multipoint -> centroid
    grouped["district"] = [districtName.get(i, "?") for i in grouped.index]
    grouped["cityId"] = [cityId(gdf, i) for i in grouped.index]
    grouped["city"] = grouped["cityId"].map(cityName)

    # --- metric CRS demo: nearest-neighbour spacing of district centroids ---
    metric = grouped.to_crs(TWD97)
    print(f"Reprojected to EPSG:{TWD97}; example district-centroid spread (m): "
          f"x={metric.geometry.x.max() - metric.geometry.x.min():,.0f}, "
          f"y={metric.geometry.y.max() - metric.geometry.y.min():,.0f}")

    ranked = grouped[grouped["n"] >= 10].sort_values("medUnitPrice", ascending=False)
    show = ranked[["city", "district", "n", "medUnitPrice"]]
    print("\nTop 8 districts by median sale unit price (NT$/m², n>=10):")
    for _, r in show.head(8).iterrows():
        print(f"  {r['city']:<16} {r['district']:<6} n={int(r['n']):<4} NT${r['medUnitPrice']:,.0f}/m²")
    print("\nCheapest 8:")
    for _, r in show.tail(8).iloc[::-1].iterrows():
        print(f"  {r['city']:<16} {r['district']:<6} n={int(r['n']):<4} NT${r['medUnitPrice']:,.0f}/m²")

    # --- plot ---
    fig, ax = plt.subplots(figsize=(7, 9))
    grouped.plot(
        ax=ax, column="medUnitPrice", cmap="YlGnBu", markersize=grouped["n"].clip(5, 120),
        legend=True, legend_kwds={"label": "Median sale unit price (NT$/m²)", "shrink": 0.5},
    )
    ax.set_title("Taiwan residential sales — median unit price by district")
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=110)
    print(f"\nSaved plot -> {OUT_PNG}")
    return 0


def cityId(gdf, districtId):
    sub = gdf[gdf["districtId"] == districtId]
    return int(sub["cityId"].iloc[0]) if len(sub) else None


if __name__ == "__main__":
    raise SystemExit(main())
