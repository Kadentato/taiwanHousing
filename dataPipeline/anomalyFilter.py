"""Drop housing-SALE records whose numbers can't be real — the last cleaning step.

Motivation: a reviewer spotted records like an "88 m² home with 5 bedrooms and 4 bathrooms".
Auditing all sales showed a small tail where the government's raw fields are internally
inconsistent: the recorded transfer area is far too small for the layout it claims, the room
counts are data-entry nonsense, or the unit price is a typo. These pollute the raw records
table and the drill-in distribution (the map medians are robust, but the individual dots and
the model shouldn't carry impossible rows). This flags and removes them.

Everything here is defined on four columns present in *every* consumer (unitPricePerM2,
livingAreaPing, bedrooms, bathrooms) so the exact same rows are dropped in the web export, the
per-district records, the geocoder and the modelling parquet. The rules only make sense for
SALE prices (rental unit price is monthly rent/m², a different scale), so callers must apply
this to sale records only.

Two families of anomaly are removed:

  Tier 1 — impossible (cannot be a real single home)
    * unit price outside NT$5,000–3,000,000 /m²  (data-entry price typos)
    * absurd room counts (>12 bedrooms, >10 bathrooms, or >18 bed+bath together)
    * the layout can't physically fit: living area below a rock-bottom floor of
      5 m²/bedroom + 2.5 m²/bathroom (bare minimums, no kitchen/hallway allowance)

  Tier 2 — implausibly tight (the "88 m² / 5bd-4ba" pattern: technically fits, but only if
    every room were unrealistically small). Two views, either triggers:
      * living area below a realistic floor of 8 m² base + 8 m²/bedroom + 3.5 m²/bathroom; or
      * room density too high — a home with >= 4 rooms (bedrooms + bathrooms) averaging under
        10 m² per room. Genuine small apartments sit at ~13-15 m²/room; the flagged "5bd/4ba
        in 88 m²" family sits at ~8, well clear of normal stock.
"""
from __future__ import annotations

import pandas as pd

M2_PER_PING = 3.305785

# price band (per m²) — matches the model's own sanity bounds
PRICE_LO, PRICE_HI = 5_000, 3_000_000
# room-count ceilings for a single dwelling
MAX_BEDROOMS, MAX_BATHROOMS, MAX_ROOMS = 12, 10, 18
# minimum floor area (m²) a layout needs: bare-physical (Tier 1) and realistic (Tier 2)
HARD_BEDROOM_M2, HARD_BATHROOM_M2 = 5.0, 2.5
SOFT_BASE_M2, SOFT_BEDROOM_M2, SOFT_BATHROOM_M2 = 8.0, 8.0, 3.5
# room density: >= this many rooms (bed+bath) averaging under this many m² each is implausible
DENSE_MIN_ROOMS, DENSE_M2_PER_ROOM = 4, 10.0


def anomalyMask(df: pd.DataFrame) -> pd.Series:
    """Boolean Series aligned to df: True = drop this SALE record as an anomaly."""
    area = df["livingAreaPing"].astype(float) * M2_PER_PING
    bd = df["bedrooms"].fillna(0).astype(float)
    ba = df["bathrooms"].fillna(0).astype(float)
    up = df["unitPricePerM2"]

    extremePrice = up.notna() & ((up < PRICE_LO) | (up > PRICE_HI))
    absurdRooms = (bd > MAX_BEDROOMS) | (ba > MAX_BATHROOMS) | ((bd + ba) > MAX_ROOMS)
    cantFit = (bd >= 1) & (area < HARD_BEDROOM_M2 * bd + HARD_BATHROOM_M2 * ba)
    tooTight = (bd >= 1) & (area < SOFT_BASE_M2 + SOFT_BEDROOM_M2 * bd + SOFT_BATHROOM_M2 * ba)
    rooms = bd + ba
    tooDense = (rooms >= DENSE_MIN_ROOMS) & (area < DENSE_M2_PER_ROOM * rooms)
    return (extremePrice | absurdRooms | cantFit | tooTight | tooDense).fillna(False)


def dropAnomalies(df: pd.DataFrame) -> pd.DataFrame:
    """Return df with sale anomalies removed (callers pass sale records only)."""
    return df[~anomalyMask(df)].copy()
