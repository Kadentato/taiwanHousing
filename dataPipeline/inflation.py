"""Taiwan CPI table for real (inflation-adjusted) prices.

General Consumer Price Index, annual average, index base 2021 = 100, from the
DGBAS (行政院主計總處). 2012–2015 are extended back from the 2016 anchor using the
published annual inflation rates (+0.79/+1.20/-0.31/+1.39%); 2025–2026 are
estimates (recent releases / projection) and are flagged as approximate. Used for
the web app's nominal→real toggle and for deflating prices in the price model.
"""

from __future__ import annotations

CPI_BASE_YEAR = 2021          # index base: CPI[2021] == 100
CPI = {
    2012: 92.98, 2013: 93.72, 2014: 94.84, 2015: 94.55,
    2016: 95.86, 2017: 96.45, 2018: 97.75, 2019: 98.30, 2020: 98.07,
    2021: 100.00, 2022: 102.95, 2023: 105.53, 2024: 107.85, 2025: 110.02, 2026: 111.9,
}


def cpiIndex(year: int) -> float:
    """CPI for a year, clamped to the table's range for out-of-range years."""
    if year in CPI:
        return CPI[year]
    years = sorted(CPI)
    return CPI[years[0]] if year < years[0] else CPI[years[-1]]


def realFactor(year: int) -> float:
    """Multiply a nominal price by this to express it in constant CPI_BASE_YEAR NT$."""
    return CPI[CPI_BASE_YEAR] / cpiIndex(year)


def toReal(price: float, year: int) -> float:
    """Deflate a nominal price to constant CPI_BASE_YEAR (2021) NT$."""
    return price * realFactor(year)
