# Predicting Taiwan house prices — point estimate + calibrated range

Given a house's metadata (location, size, age, floor, layout, ...) predict its
transaction price, with a **range** you can trust, validated strictly out-of-time.
Two deliverables:

- `crossValidate.py` — proves the method: rolling-origin cross-validation, so
  "how accurate" and "how wide a range" are measured, not assumed.
- `predictPrices.py` — the tool: feed it a CSV of houses, get a point price and
  50% / 80% / 95% ranges per house, in nominal NT$.

## Data (`buildDataset.py` → `data/sales.parquet`)
One row per **sale**, de-duplicated on the `編號` serial across all 51 season
folders (`../fetchHistory.py`). Features carried for the model:

- location: `cityCode`, `districtEn` (district fixed effects)
- size: `livingAreaPing` (net of parking), `landAreaPing`, `mainBuildingRatio` (公設 loading)
- age: `buildingAgeYears`
- floor: `transferFloor`, `totalFloors` (→ a relative floor-position feature)
- layout: `bedrooms`, `livingRooms`, `bathrooms`, `hasCompartments`
- flags: `hasParking`, `hasElevator`, `hasManagementOrg`
- categoricals: `buildingType`, `mainUse`, `mainMaterial`
- target: `unitPricePerM2` (NT$/m², government parking-adjusted)

## Inflation
Prices are deflated to **constant 2021 NT$** with the DGBAS CPI
(`../dataPipeline/inflation.py`), so the model's time trend is *real* appreciation,
not monetary inflation. Predictions are converted back to the target month's
nominal NT$ for reporting. This alone removed most of the systematic
under-prediction bias (Feb-2024 city-median error 14.5% → 9.5%).

## Models (each trained only on the past)
- `globalMedian` — one national number (floor baseline)
- `cityTypeMedian` — median of the sale's city × building-type (location only)
- `hedonicCity` — regression on all features + city FE + city-specific time trend
- `hedonicDistrict` — same, plus **district** fixed effects (the headline model)

Going city → district is a big lever: on the Feb-2024 holdout, typical error
(median APE) dropped ~24% → ~17% and R²(log) rose ~0.47 → ~0.62.

## Ranges — split-conformal prediction intervals
Instead of one number, we output "X% confident the price is in [lo, hi]". The band
width is the (1-α) quantile of absolute log-residuals, calibrated on the **most
recent months predicted out-of-sample** (matched to the forecasting horizon).
Verified out-of-time on Feb-2024 the stated confidence holds (a 95% range really
contains ~95% of sales). Calibrated band widths (registry-only features):

| level | width | notes |
|---|--:|---|
| 50% "most likely" | ±21% | tight, right half the time |
| 80% "reasonable"  | ±45% | practical appraisal band |
| 95% "high-confidence" | ±101% | wide, almost never wrong |

The bands are wide because ~38% of price variation is idiosyncratic (renovation,
exact micro-location/MRT distance, view, negotiation) and **not in the registry** —
a data ceiling, not a model flaw.

**Locally-weighted (adaptive) intervals — `adaptiveIntervals.py`.** A second
"difficulty" model scales the band per property (`mu(x) ± q·sigma(x)`), so standard
flats in data-rich districts get a tight band and oddballs get a wide one, coverage
preserved. Validated out-of-time: coverage held (50/79/94% at 50/80/95%) while a
*standard* home's 80% band roughly halves (±30% → ±15%) and unusual ones correctly
widen (±57%). It reallocates width honestly rather than shrinking the average.
**Now shipped in the live web predictor** — `exportPredictor.py` serialises the
difficulty model's trees alongside the price model, and `predictor.js` walks it for
a per-property band (e.g. a standard Taipei tower shows ±23% at 80%, a rural oddball
±31%).

## Run
```bash
python fetchHistory.py --from 2012 --to 2025Q1      # once, from the project root
python modeling/buildDataset.py                     # -> modeling/data/sales.parquet

python modeling/crossValidate.py                    # validate: metrics + output/*.png

python modeling/predictPrices.py --template         # write a blank input CSV
python modeling/predictPrices.py --in houses.csv    # -> houses.valued.csv

python modeling/clusterCv.py                         # cluster-sampling CV: ±10% hit-rate by region
python modeling/developmentValuation.py --city "Taipei City" --district "Da'an District" \
       --type residentialTower --size 100 --units 40 --year 2028   # value a new-build project
python modeling/developmentValuation.py --city "Taichung City" --sweep district   # rank lots
```

## For developers (revenue side): `developmentValuation.py`
Reframes the price model as a build-decision tool. It values a **development** (N
new units), not one house — the per-home noise averages out, leaving the honest
risk = the *district-cohort* model error (±~9%, out-of-time, which does **not**
diversify). Consultant-review upgrades:
- **New-build premium from pre-sale data:** scales the model's age-0 output by the
  market premium of pre-sale (預售) prices over newly-completed resales (age≤2),
  per city (Taipei ×1.26, Taichung ×1.43). Build the input once with
  `python modeling/buildDataset.py --txn-suffix b --out modeling/data/presale.parquet`.
- **Saleable-area (公設) basis:** revenue is on the deed/saleable area (含公設); a
  realistic per-type 公設 (~33% for towers) is baked in, override with `--gongshe`.
- **Scenarios:** Bull / Base / Bear(stress) price paths to the sale year, with a
  **P10 underwriting downside**. Base growth is data-derived and override-able
  (`--growth`) — it's the biggest lever, so it's explicit.
- **Absorption / velocity:** each lot shows how many comparable new units of that
  product were **pre-sold per year** in the district (demand depth) + estimated
  sell-out time, flagging **thin/unproven** markets (0 = no proven demand for that
  product there) and oversupply (project > 1.5× throughput). LVR is completed
  transactions, not live listings, so this is market *throughput*, not sold/available.

Output is revenue only (subtract land/build/soft costs for margin). `--sweep type`
ranks what to build on a lot; `--sweep district` ranks which lot (each with the P10
downside).

**Validation — `backtestDevelopment.py`** (rolling-origin, out-of-time, per building
type incl. houses/透天): the forward Base projection has ~14% point error, and the
first-cut scenario band was badly overconfident (Bear..Bull covered realized prices
only ~29%). The real forward spread is ~0.14 log-vol per √year (±21% at 2yr, ±24% at
3yr) with a +9–14% upward bias in the 2016–24 boom — so the tool's 80% band is now
**calibrated to that** (`FWD_VOL_PER_YR`), giving realistic width. The new-build
premium is stable out-of-time (~9% error) and differs by product (tower ×1.19, house
×1.08). `clusterCv.py` reports the ±10%/±20% hit-rate by region (the honest way to
read individual accuracy; don't tune to one draw).
`predictPrices.py` input columns (case-insensitive; missing optional ones imputed):
location (`cityEn`/`cityCode`/`cityZh` + `districtZh`/`districtEn`), `livingAreaPing`
(or `areaM2`), `buildingAgeYears`, and optionally floor/layout/flags/categoricals.
Flags: `--as-of YYYY-MM`, `--out`, `--train-cap`.

## Honesty
- Feature NaNs are median/0-imputed (features only, never the target).
- Unit prices clipped to 5k–3M NT$/m² to drop data-entry errors.
- The hedonic time trend is linear; a gradient-boosted variant (interactions,
  nonlinearity) is the next lever to narrow the ranges without new data.
