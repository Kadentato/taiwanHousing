"""Seal the hairline white "cracks" inside the shipped city/region choropleth polygons.

The web map draws cityAggregates.geojson / regionAggregates.geojson as filled polygons.
Because the bundled township boundaries don't tile perfectly, the dissolved city/region
shapes contain thin sliver gaps that show through as white streaks when you zoom in. This
applies the same morphological close as the build pipeline (dataPipeline.spatialBuilder.sealGaps)
directly to the shipped GeoJSON, so we don't have to rebuild the multi-GB database just to
regenerate two small files. Re-simplifies and rounds coordinates afterwards so the sealed
files stay as light as the originals.

    python spatialAnalysis/sealAggregateGaps.py
"""
from __future__ import annotations

import json
import os
import sys

import geopandas as gpd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dataPipeline.spatialBuilder import sealGaps, CITY_SIMPLIFY, WGS84  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "webApp", "dataFiles")
NDIGITS = 5  # ~1 m; plenty for a ~200 m-simplified choropleth, and keeps the files small


def _round(coords):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], NDIGITS), round(coords[1], NDIGITS)]
    return [_round(c) for c in coords]


def main() -> int:
    for name in ("cityAggregates.geojson",):
        path = os.path.join(DATA, name)
        gdf = gpd.read_file(path).set_crs(WGS84, allow_override=True)
        gdf["geometry"] = sealGaps(gdf.geometry)
        # buffer adds rounded-join vertices; simplify strips them back out so the file stays light
        gdf["geometry"] = gdf.geometry.simplify(CITY_SIMPLIFY, preserve_topology=True)
        fc = json.loads(gdf.to_json())  # native-typed FeatureCollection (handles numpy props)
        for feat in fc["features"]:
            feat["geometry"]["coordinates"] = _round(feat["geometry"]["coordinates"])
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(fc, fh, ensure_ascii=False, separators=(",", ":"))
        print(f"sealed {name} ({len(gdf)} polygons, {os.path.getsize(path) / 1e3:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
