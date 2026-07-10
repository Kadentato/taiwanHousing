"""Advanced statistics for the exporter: spatial autocorrelation and a hedonic
price model. Kept separate so the heavy libraries (esda/libpysal, statsmodels)
are only imported when these run.
"""

from __future__ import annotations

from typing import List, Optional

import numpy as np


def districtMoran(districtRows: List[dict]) -> Optional[dict]:
    """Global Moran's I + local LISA clusters for a per-district value.

    districtRows: [{districtId, lat, lon, value}]. Uses k-nearest-neighbour
    spatial weights on centroids (no polygon adjacency needed). Returns global I,
    its pseudo p-value, and a per-district cluster label (HH/LL/HL/LH/ns).
    """
    from esda.moran import Moran, Moran_Local
    from libpysal.weights import KNN

    rows = [r for r in districtRows if r.get("value") is not None and r.get("lat") is not None]
    if len(rows) < 12:
        return None
    coords = np.array([[r["lon"], r["lat"]] for r in rows])
    y = np.array([float(r["value"]) for r in rows])

    w = KNN.from_array(coords, k=min(6, len(rows) - 1))
    w.transform = "r"
    glob = Moran(y, w, permutations=999)
    loc = Moran_Local(y, w, permutations=999)

    quad = {1: "HH", 2: "LH", 3: "LL", 4: "HL"}
    lisa = {}
    for i, r in enumerate(rows):
        lisa[r["districtId"]] = quad[loc.q[i]] if loc.p_sim[i] < 0.05 else "ns"
    return {"I": round(float(glob.I), 3), "p": round(float(glob.p_sim), 4), "n": len(rows), "lisa": lisa}


def hedonicRegression(df) -> Optional[dict]:
    """OLS hedonic model: log(dwelling price) ~ size + age + rooms + parking +
    elevator + building type + city. Returns R², n, and each term's % price effect.

    The dependent variable is the price **net of the parking price** (total −
    parking), so the "has parking" coefficient reflects the amenity premium rather
    than mechanically absorbing the parking space's own cost.
    """
    import statsmodels.formula.api as smf

    d = df.copy()
    d["dwellingPrice"] = d["totalPrice"] - d["parkingPrice"].fillna(0)
    d = d[(d["dwellingPrice"] > 0) & (d["livingAreaPing"] > 0) & d["buildingType"].notna()]
    if len(d) < 200:
        return None
    d["logPrice"] = np.log(d["dwellingPrice"])
    d["age"] = d["buildingAgeYears"].fillna(d["buildingAgeYears"].median())
    d["beds"] = d["bedrooms"].fillna(0)
    d["baths"] = d["bathrooms"].fillna(0)
    d["parking"] = d["hasParking"].fillna(0)
    d["elevator"] = d["hasElevator"].fillna(0)

    model = smf.ols(
        "logPrice ~ livingAreaPing + age + beds + baths + parking + elevator"
        " + C(buildingType) + C(cityId)",
        data=d,
    ).fit()

    labels = {
        "livingAreaPing": "per extra ping of floor area",
        "age": "per extra year of building age",
        "beds": "per extra bedroom",
        "baths": "per extra bathroom",
        "parking": "has parking (vs none)",
        "elevator": "has elevator (vs none)",
    }
    terms = []
    for key, label in labels.items():
        if key in model.params:
            coef = float(model.params[key])
            terms.append({
                "term": label,
                "pctEffect": round((np.exp(coef) - 1) * 100, 1),  # log-linear -> % effect
                "p": round(float(model.pvalues[key]), 4),
            })
    return {"r2": round(float(model.rsquared), 3), "n": int(model.nobs), "terms": terms}
