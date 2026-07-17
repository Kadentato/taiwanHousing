"""Render the animated README hero: a year-by-year choropleth of Taiwan shaded by each
district's median housing sale price. Unlike the static map (makeDistrictMap.py), which
pools every sale from 2012-2026 onto one frame regardless of when it happened, this shows
the market *evolving* — one frame per year, on a FIXED colour scale so a district getting
redder over time means its prices genuinely rose (not just a re-normalised palette).

Reads per-transaction sales (modeling/data/sales.parquet) for per-year medians, and the
shipped township polygons for geometry.

    python spatialAnalysis/makePriceGif.py
"""
from __future__ import annotations

import io
import os
import re

import matplotlib
matplotlib.use("Agg")
import geopandas as gpd  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402
from PIL import Image  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "taiwanPriceTimelapse.gif")
PARQUET = os.path.join(ROOT, "modeling", "data", "sales.parquet")

YEAR_MIN, YEAR_MAX = 2012, 2026          # mandatory reporting began Aug 2012; 2026 is partial
MIN_SALES = 8                             # a district-year needs this many sales to be coloured
FRAME_MS, HOLD_MS = 650, 1600             # per-frame duration; last frame lingers


def norm(s):
    """Match the boundary file's town names to the sales' district names: strip the admin
    suffix (中壢市 vs 中壢區) and any '(…)' note, and unify the 臺/台 variant."""
    s = re.sub(r"\(.*?\)", "", str(s)).replace("臺", "台")
    return re.sub(r"[鄉鎮市區]$", "", s).strip()


def main() -> int:
    polys = gpd.read_file(os.path.join(ROOT, "geoReference", "townshipBoundaries.geojson"))
    polys["key"] = [(r.cityCode, norm(r.town)) for r in polys.itertuples()]

    df = pd.read_parquet(PARQUET, columns=["saleYear", "cityCode", "districtZh", "unitPricePerM2"])
    df = df[(df.saleYear >= YEAR_MIN) & (df.saleYear <= YEAR_MAX) & df.unitPricePerM2.notna()]
    df["key"] = list(zip(df.cityCode, df.districtZh.map(norm)))

    # per (year, district) median price, dropping thin district-years to keep medians stable
    grp = df.groupby(["saleYear", "key"])["unitPricePerM2"]
    med = grp.median()
    cnt = grp.size()
    med = med[cnt >= MIN_SALES]

    # ONE fixed colour scale for every frame, from the pooled distribution of the per-year
    # district medians — so the palette never re-normalises and change is real change.
    allv = med.values
    vmin, vmax = float(np.percentile(allv, 5)), float(np.percentile(allv, 95))
    print(f"fixed scale: {vmin/1000:.0f}k - {vmax/1000:.0f}k NT$/m^2")

    frames = []
    for year in range(YEAR_MIN, YEAR_MAX + 1):
        yr = med.loc[year] if year in med.index.get_level_values(0) else pd.Series(dtype=float)
        lookup = yr.to_dict()
        polys["price"] = [lookup.get(k) for k in polys["key"]]
        nmatched = polys["price"].notna().sum()

        fig, ax = plt.subplots(figsize=(5.7, 7.6))
        fig.patch.set_facecolor("white")
        # grey landmass base seals hairline gaps; stroke each polygon in its own fill colour
        polys.plot(ax=ax, color="#eceff3", edgecolor="face", linewidth=0.4)
        polys[polys["price"].notna()].plot(
            ax=ax, column="price", cmap="YlOrRd", vmin=vmin, vmax=vmax,
            edgecolor="face", linewidth=0.4, legend=True,
            legend_kwds={"label": "Median sale price (NT$/m²)", "shrink": 0.42,
                         "format": FuncFormatter(lambda x, _: f"{x / 1000:.0f}k")})
        ax.set_xlim(119.3, 122.05)
        ax.set_ylim(21.85, 25.35)
        ax.set_axis_off()
        ax.set_title("Taiwan — median housing sale price by district", fontsize=12, pad=6)
        label = f"{year}*" if year == YEAR_MAX else f"{year}"
        ax.text(0.03, 0.965, label, transform=ax.transAxes, fontsize=34, fontweight="bold",
                va="top", ha="left", color="#7a1a1a")
        if year == YEAR_MAX:
            ax.text(0.03, 0.905, "partial year", transform=ax.transAxes, fontsize=9,
                    va="top", ha="left", color="#7a1a1a")
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=110, facecolor="white")
        plt.close(fig)
        buf.seek(0)
        frames.append(Image.open(buf).convert("RGB"))
        print(f"{year}: {nmatched} districts")

    # quantise to a shared 256-colour palette so the GIF stays small and flicker-free
    pal = frames[0].quantize(colors=256, method=Image.MEDIANCUT)
    quant = [f.quantize(palette=pal, dither=Image.NONE) for f in frames]
    durations = [FRAME_MS] * (len(quant) - 1) + [HOLD_MS]
    quant[0].save(OUT, save_all=True, append_images=quant[1:], loop=0,
                  duration=durations, disposal=2, optimize=True)
    print(f"wrote {OUT} ({os.path.getsize(OUT) / 1e6:.1f} MB, {len(quant)} frames)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
