"""Load the bundled district centroids and resolve coordinates for a transaction.

Reads ``geoReference/districtCentroids.csv`` (produced once by
``setupGeoReference.py``) so the build runs fully offline. Lookups are keyed by
``(cityCode, districtName)``; an unknown district falls back to the mean of its
city's known district centroids so a record always gets a location.
"""

from __future__ import annotations

import csv
import os
from typing import Dict, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
CENTROIDS_PATH = os.path.join(HERE, "..", "geoReference", "districtCentroids.csv")


class GeoLookup:
    def __init__(self, centroidsPath: str = CENTROIDS_PATH):
        self._byDistrict: Dict[Tuple[str, str], Tuple[float, float, str]] = {}
        self._cityPoints: Dict[str, list] = {}
        with open(centroidsPath, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                code = row["cityCode"].strip()
                district = row["districtName"].strip()
                lat, lon = float(row["lat"]), float(row["lon"])
                self._byDistrict[(code, district)] = (lat, lon, row["matchType"])
                self._cityPoints.setdefault(code, []).append((lat, lon))

    def cityCentroid(self, cityCode: str) -> Optional[Tuple[float, float]]:
        pts = self._cityPoints.get(cityCode)
        if not pts:
            return None
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))

    def resolve(self, cityCode: str, districtName: str) -> Tuple[Optional[float], Optional[float], str]:
        """Return (lat, lon, source) for a (city, district); 'missing' if unknown."""
        hit = self._byDistrict.get((cityCode, (districtName or "").strip()))
        if hit:
            return hit
        city = self.cityCentroid(cityCode)
        if city:
            return (city[0], city[1], "cityFallback")
        return (None, None, "missing")
