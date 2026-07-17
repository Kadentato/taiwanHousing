"""Render the animated README hero: a quarter-by-quarter choropleth of Taiwan shaded by each
district's median housing sale price. Unlike the static map (makeDistrictMap.py), which pools
every sale from 2012-2026 onto one frame regardless of when it happened, this shows the market
*evolving* on a FIXED colour scale — so a district getting redder over time means its prices
genuinely rose, not a re-normalised palette.

Cadence is quarterly, but each frame shows the median over the *trailing 12 months* ending that
quarter. That keeps ~300 districts lit every frame and turns the animation into a smooth sweep
instead of the flicker you'd get from thin single-quarter medians in small districts.

Reads per-transaction sales (modeling/data/sales.parquet) for the medians, and the shipped
township polygons for geometry.

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

# Real data runs Aug 2012 (2012 Q3) -> mid-2026. Show one frame per quarter, each a trailing
# 12-month window; start where the first full window closes (2013 Q2) so every frame is a true
# trailing year, and stop at the last quarter with data (2026 Q2).
FIRST_QI, LAST_QI = 2013 * 4 + 1, 2026 * 4 + 1   # qi = year*4 + (quarter-1)
MIN_SALES = 8                                     # a district needs this many sales in the window
FRAME_MS, HOLD_MS = 190, 1600                     # per-frame duration; last frame lingers


def norm(s):
    """Match the boundary file's town names to the sales' district names: strip the admin
    suffix (中壢市 vs 中壢區) and any '(…)' note, and unify the 臺/台 variant."""
    s = re.sub(r"\(.*?\)", "", str(s)).replace("臺", "台")
    return re.sub(r"[鄉鎮市區]$", "", s).strip()


def main() -> int:
    polys = gpd.read_file(os.path.join(ROOT, "geoReference", "townshipBoundaries.geojson"))
    polys["key"] = [(r.cityCode, norm(r.town)) for r in polys.itertuples()]

    df = pd.read_parquet(PARQUET, columns=["saleYear", "saleMonth", "cityCode", "districtZh",
                                           "unitPricePerM2"])
    df = df[df.unitPricePerM2.notna() & df.saleMonth.notna()].copy()
    df["qi"] = df.saleYear.astype(int) * 4 + ((df.saleMonth.astype(int) - 1) // 3)
    df = df[(df.qi >= FIRST_QI - 3) & (df.qi <= LAST_QI)]        # keep enough lead for the window
    df["key"] = list(zip(df.cityCode, df.districtZh.map(norm)))

    # A trailing median is the median of the raw values in the window, NOT an average of quarterly
    # medians, so keep the rows and re-median per frame.
    frame_qis = list(range(FIRST_QI, LAST_QI + 1))

    # One fixed colour scale for every frame, from the pooled distribution of all the trailing
    # medians we're about to draw — so the palette never re-normalises and change is real change.
    windows = {}
    for qi in frame_qis:
        w = df[(df.qi <= qi) & (df.qi >= qi - 3)]
        med = w.groupby("key")["unitPricePerM2"].median()
        cnt = w.groupby("key")["unitPricePerM2"].size()
        windows[qi] = med[cnt >= MIN_SALES]
    allv = np.concatenate([m.values for m in windows.values()])
    vmin, vmax = float(np.percentile(allv, 5)), float(np.percentile(allv, 95))
    print(f"{len(frame_qis)} frames, fixed scale {vmin/1000:.0f}k-{vmax/1000:.0f}k NT$/m^2")

    frames = []
    for qi in frame_qis:
        year, q = qi // 4, qi % 4 + 1
        lookup = windows[qi].to_dict()
        polys["price"] = [lookup.get(k) for k in polys["key"]]

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
        ax.text(0.03, 0.965, f"{year}", transform=ax.transAxes, fontsize=32, fontweight="bold",
                va="top", ha="left", color="#7a1a1a")
        ax.text(0.035, 0.905, f"Q{q}", transform=ax.transAxes, fontsize=15, fontweight="bold",
                va="top", ha="left", color="#7a1a1a")
        ax.text(0.03, 0.055, "trailing 12 months", transform=ax.transAxes, fontsize=8.5,
                va="bottom", ha="left", color="#555")
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100, facecolor="white")
        plt.close(fig)
        buf.seek(0)
        frames.append(Image.open(buf).convert("RGB"))
    print(f"rendered {len(frames)} frames at {frames[0].size}")

    # quantise to a shared 256-colour palette so the GIF stays small and flicker-free
    pal = frames[len(frames) // 2].quantize(colors=256, method=Image.MEDIANCUT)
    quant = [f.quantize(palette=pal, dither=Image.NONE) for f in frames]
    durations = [FRAME_MS] * (len(quant) - 1) + [HOLD_MS]
    quant[0].save(OUT, save_all=True, append_images=quant[1:], loop=0,
                  duration=durations, disposal=2, optimize=True)
    print(f"wrote {OUT} ({os.path.getsize(OUT) / 1e6:.1f} MB, {len(quant)} frames)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
