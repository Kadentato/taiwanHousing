"""Spatial tests: WKT round-trips and validity of the bundled geometry."""

import os
import sys

import geopandas as gpd
import pytest
from shapely.geometry import Point

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOUNDARIES = os.path.join(ROOT, "geoReference", "townshipBoundaries.geojson")


def testWktRoundTrip():
    pt = Point(121.5654, 25.0330)  # Taipei
    wkt = pt.wkt
    restored = gpd.GeoSeries.from_wkt([wkt]).iloc[0]
    assert restored.equals_exact(pt, tolerance=1e-9)


@pytest.mark.skipif(not os.path.exists(BOUNDARIES), reason="run setupGeoReference.py first")
def testBoundariesValidAndDissolvable():
    gdf = gpd.read_file(BOUNDARIES).set_crs(4326, allow_override=True)
    assert len(gdf) > 300
    assert gdf.geometry.is_valid.all()
    assert "cityCode" in gdf.columns
    # Dissolving townships into the 21 cities should yield valid polygons.
    cities = gdf.dissolve(by="cityCode")
    assert len(cities) == 21
    assert cities.geometry.is_valid.all()
    # Centroid computation in the metric CRS should stay inside Taiwan's bbox.
    centroids = cities.to_crs(3826).geometry.centroid.to_crs(4326)
    assert centroids.x.between(118, 122.5).all()
    assert centroids.y.between(21, 26.5).all()
