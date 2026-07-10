# Model Card — Taiwan housing price model

One model, three audiences (a stats classroom, a public valuation tool, a construction firm's
build decisions). Because one of those commits capital, this card is the contract: what the model
does, what it assumes, how well it's been shown to work, and where it must **not** be trusted.

## Version / provenance
Stamped in `predictor.json.version`: `builtOn`, `dataThrough`, `trainRows`, `model`.
Current: built **2026-07**, data through **2026-03** (latest *complete* month; the tail is trimmed for
disclosure lag). The model is a deterministic function of `sourceData` + this code — reproduce with the
pipeline below. A decision should record the model version it used. *(The firm CLI reports one month
earlier — "Base 2026-02" — because it deliberately holds out the latest complete month to measure its own
cohort error out-of-sample; that offset is by design, not data drift.)*

## What it predicts
The **unit price (NT$/m²)** of a housing sale from its metadata (location, size, age, floor, layout, type,
parking/elevator/management). Everything else is built on this: individual valuation (× area → total),
cohort/development valuation, and forward projection.

## Data & training
Taiwan MOI **實價登錄 (LVR)** open data, 2012 Q3 → present, de-duplicated on serial number. Housing **sales**
only. Trained on ≤ 400k **arm's-length** sales (related-party transfers dropped) in constant 2021 NT$ (DGBAS CPI).

## Models
1. **Price model** — `HistGradientBoosting` on log real unit price; location target-encoded (city, district).
2. **Interval** — split-conformal, **locally-weighted**: a second "difficulty" model σ(x) scales the band per
   property, so standard homes get a tighter range and unusual ones wider. Calibrated one-step-ahead.
3. **Development layer** (`developmentValuation.py`, firm only) — new-build (age 0) × a **pre-sale premium**
   (per city/type, from `presale.parquet`), on a saleable-area (公設) basis, **projected to the sale year** with
   Bull/Base/Bear growth, a **backtest-calibrated forward band**, and an absorption/velocity read.

## Validated performance (out-of-time)
- Individual: **medAPE ~14–16%**, ~60% within ±20%, R²(log) ~0.7 (`crossValidate.py`, `clusterCv.py`).
- Intervals: coverage on target across 2016–2024 regimes; adaptive band ~±22% (standard) to ~±37% (house)
  at 80%, per-segment calibrated (`adaptiveIntervals.py`).
- Cohort/city median: **~7–9% MAPE** — much better than a single unit.
- Forward (development): point ~14%; scenario band recalibrated to the measured spread (~0.14 log-vol/√yr,
  ±20% at 2 yr / ±28% at 4 yr) after the first-cut band under-covered (`backtestDevelopment.py`).

## Known limitations
- **~30% of individual variation is idiosyncratic** (renovation, exact spot, floor view, negotiation) and is
  **not in the registry** — a data floor, not a model flaw. Location resolves only to district/road, not a lot.
- **Forward projection is a trend + scenarios**, not a proper time-series model, and can't foresee policy
  shocks; it carried a +9–14% upward bias in the 2016–24 rise. The forward band is the dominant risk.
- **Disclosure lag**: recent months undercount until later releases; "as-of" trails today by a quarter or two.
- Pre-sale premium may carry composition bias (new 重劃區 vs the resale mix). Absorption = market *throughput*
  (units pre-sold/yr), not a sold-÷-available rate.

## Intended use — and **do not use** for
- ✅ Compare/shortlist areas & products; value a *development* (cohort); size a deal to the **P10 downside**;
  teach time series against the baselines.
- ❌ A per-unit legal appraisal (individual error is large). ❌ Betting on the Base forward number — **underwrite
  to Bear/P10**. ❌ Treating `sales.parquet` as "raw" data (it's plausible-year filtered & cleaned).

## Integrity guardrail (non-negotiable)
The firm has an incentive for optimistic values; students are learning "the right way". So: every interval
tightening must pass the back-test, every assumption (growth, premium, 公設) is explicit and override-able,
and the P10/underwriting framing stays. The same honesty protects the capital decision *and* the classroom.

## Reproduce / refresh
```
python fetchHistory.py                 # pull latest LVR seasons
python modeling/buildDataset.py        # -> sales.parquet   (+ --txn-suffix b for presale.parquet)
python buildDatabase.py --seasons-dir sourceData --sales-only --no-subtables   # web DB + exports
python publishSampleDb.py ; python exportSeriesCsv.py ; python modeling/exportPredictor.py
```
Then re-run `crossValidate.py` / `backtestDevelopment.py` and only ship if coverage still holds.
