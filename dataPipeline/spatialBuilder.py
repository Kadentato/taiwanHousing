"""Spatial layer (geopandas + Shapely).

Reads the bundled township polygons, dissolves them into city and region
geometries, computes accurate centroids (in EPSG:3826), and writes the geometry
back into the ``cities`` / ``regions`` tables as WKT. House and district point
geometries are already written by the loader.
"""

from __future__ import annotations

import os
import sqlite3

import geopandas as gpd
import pandas as pd

from . import valueMappings as vm

HERE = os.path.dirname(os.path.abspath(__file__))
BOUNDARIES_PATH = os.path.join(HERE, "..", "geoReference", "townshipBoundaries.geojson")
WGS84 = 4326
TWD97 = 3826
CITY_SIMPLIFY = 0.002      # ~200 m, keeps dissolved polygons light for the web


def sealGaps(geom: gpd.GeoSeries, closeM: float = 120.0, netBufferM: float = 30.0) -> gpd.GeoSeries:
    """Close the hairline "cracks" inside a dissolved polygon.

    The bundled township polygons don't tile perfectly, so ``dissolve`` leaves thin
    slivers of empty space inside each merged city/region that render as white streaks
    (the basemap showing through). A morphological close (buffer out ``closeM`` metres to
    bridge the slivers, then back) fills them, and we keep a small net outward buffer so
    neighbouring polygons overlap by a hair — guaranteeing no white seam between them at
    any zoom. Done in TWD97 so the distances are real metres.
    """
    m = geom.to_crs(TWD97)
    sealed = m.buffer(closeM).buffer(-(closeM - netBufferM))
    return sealed.to_crs(WGS84)


def buildSpatial(conn: sqlite3.Connection, boundariesPath: str = BOUNDARIES_PATH) -> dict:
    townships = gpd.read_file(boundariesPath).set_crs(WGS84, allow_override=True)

    # --- city polygons + centroids ---
    cityPoly = townships.dissolve(by="cityCode")
    # Seal internal sliver gaps first, then simplify — simplify also strips the extra vertices the
    # buffer adds, so the web polygons stay light.
    cityPoly["geometry"] = sealGaps(cityPoly.geometry)
    cityPoly["geometry"] = cityPoly.geometry.simplify(CITY_SIMPLIFY, preserve_topology=True)
    cityCentroid = cityPoly.to_crs(TWD97).geometry.centroid.to_crs(WGS84)

    cur = conn.cursor()
    for code, geom, pt in zip(cityPoly.index, cityPoly.geometry, cityCentroid):
        cur.execute(
            "UPDATE cities SET centroidLat=?, centroidLon=?, geometryWkt=? WHERE fileCode=?",
            (round(pt.y, 6), round(pt.x, 6), geom.wkt, code),
        )

    # --- region polygons (dissolve cities by region) ---
    cityFlat = cityPoly.reset_index()
    cityFlat["regionKey"] = cityFlat["cityCode"].map(lambda c: vm.CITY_BY_CODE[c][2])
    regionPoly = cityFlat.dissolve(by="regionKey")
    regionPoly["geometry"] = sealGaps(regionPoly.geometry)
    regionPoly["geometry"] = regionPoly.geometry.simplify(CITY_SIMPLIFY, preserve_topology=True)
    for regionKey, geom in zip(regionPoly.index, regionPoly.geometry):
        cur.execute(
            "UPDATE regions SET geometryWkt=? WHERE regionKey=?", (geom.wkt, regionKey)
        )
    conn.commit()
    return {"cities": len(cityPoly), "regions": len(regionPoly)}


def loadHousesGeoDataFrame(conn: sqlite3.Connection, where: str = "") -> gpd.GeoDataFrame:
    """Load houses as a GeoDataFrame (Point geometry) for spatial analysis."""
    clause = f" WHERE {where}" if where else ""
    df = pd.read_sql_query(
        "SELECT houseId, cityId, districtId, transactionType, targetType, totalPrice,"
        " unitPricePerM2, buildingAreaPing, bedrooms, bathrooms, buildingType,"
        " centroidLat, centroidLon, geometryWkt FROM houses" + clause,
        conn,
    )
    df = df[df["geometryWkt"].notna()]
    geometry = gpd.GeoSeries.from_wkt(df["geometryWkt"])
    return gpd.GeoDataFrame(df.drop(columns=["geometryWkt"]), geometry=geometry, crs=WGS84)
