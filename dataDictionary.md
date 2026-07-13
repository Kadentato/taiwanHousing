# Data Dictionary (Mandarin source → English schema)

How the LVR source columns map to the SQLite schema, plus every enumeration translation.
Database identifiers are lowerCamelCase. Raw Mandarin is preserved in `*Zh` columns where a
faithful translation isn't 1:1.

## `houses` (one row per transaction)

| Source column (ZH) | Schema column | Notes |
|---|---|---|
| 鄉鎮市區 | `districtId` → `districts.nameZh` | district, keyed by (city, name) |
| (file code) | `cityId` → `cities` | a–x → city/county |
| 交易標的 | `targetType` | see *Target type* below |
| (file suffix a/b/c) | `transactionType` | sale / presale / rental |
| 土地位置建物門牌 | `address` | kept verbatim (Mandarin address) |
| 交易年月日 / 租賃年月日 | `saleDate` (+ `saleYear/Month/Quarter`) | ROC → ISO |
| 總價元 / 總額元 | `totalPrice` | NTD (rental = monthly rent) |
| 單價元平方公尺 | `unitPricePerM2` | NTD per m² |
| 土地移轉總面積平方公尺 / 土地面積平方公尺 | `landAreaM2`, `landAreaPing` | ping = m² / 3.305785 |
| 建物移轉總面積平方公尺 / 建物總面積平方公尺 | `buildingAreaM2`, `buildingAreaPing` | |
| 主建物面積 / 附屬建物面積 / 陽台面積 | `mainBuildingAreaM2` / `auxBuildingAreaM2` / `balconyAreaM2` | sale only |
| 建物現況格局-房 / -廳 / -衛 | `bedrooms` / `livingRooms` / `bathrooms` | |
| 建物現況格局-隔間 | `hasCompartments` | 有/無 → 1/0 |
| 總樓層數 | `totalFloors` | Chinese numeral → int |
| 移轉層次 / 租賃層次 | `transferFloor` | parsed floor (null if multi/complex) |
| 建物型態 | `buildingType` | see *Building type* |
| 主要用途 | `mainUse` (+ `mainUseZh`) | common term else `other` |
| 主要建材 | `mainMaterial` (+ `mainMaterialZh`) | common term else `other` |
| 建築完成年月 | `buildCompletionDate`, `buildingAgeYears` | ROC → ISO; age computed |
| 有無管理組織 | `hasManagementOrg` | 有/無 → 1/0 |
| 電梯 / 有無電梯 | `hasElevator` | sale/rental; presale null |
| 車位類別 | `parkingType` (+ `hasParking`) | see *Parking type* |
| 車位移轉總面積平方公尺 / 車位面積平方公尺 | `parkingAreaM2` | |
| 車位總價元 / 車位總額元 | `parkingPrice` | |
| 有無附傢俱 | `hasFurniture` | rental only |
| 出租型態 / 租賃期間 / 租賃住宅服務 | `rentalType` / `rentalStartDate`+`rentalEndDate` / `rentalServices` | rental only |
| 建案名稱 / 解約情形 | `projectName` / `terminationStatus` | presale only |
| 備註 | `note` | verbatim |
| 編號 | `serialNumber` | source join key |
| — | `centroidLat`, `centroidLon`, `geometryWkt` | district centroid, EPSG:4326 |
| — | `latitude`, `longitude`, `nearestSchoolName`, `nearestSchoolDistanceM`, `schoolsWithin1km` | nullable enrichment placeholders |

## Sub-tables

**`houseBuildings`** (建物 `*_build.csv`): `serialNumber`, `ageYears`(屋齡), `areaM2`(建物移轉面積平方公尺),
`mainUse`/`mainUseZh`, `material`/`materialZh`, `completionDate`(建築完成日期, CJK→ISO),
`totalFloors`(總層數), `buildingLayer`(建物分層), `transferStatus`(移轉情形).

**`houseLandParcels`** (土地 `*_land.csv`): `landPosition`(土地位置), `areaM2`(土地移轉面積平方公尺),
`zoning`(使用分區或編定), `shareNumerator`(權利人持分分子), `shareDenominator`(權利人持分分母),
`transferStatus`(移轉情形), `parcelNumber`(地號).

**`houseParking`** (停車場 `*_park.csv`): `parkingType`/`parkingTypeZh`(車位類別), `price`(車位價格),
`areaM2`(車位面積平方公尺), `floor`(車位所在樓層).

## Enumerations

**Target type (交易標的 → `targetType`)**
`房地(土地+建物)+車位`→`houseLandParking` · `房地(土地+建物)`→`houseLand` · `土地`→`landOnly` ·
`建物`→`buildingOnly` · `車位`→`parkingOnly` · `租賃房屋`→`rentalHousing` · `租賃房屋+車位`→`rentalHousingParking`.
*Housing views* keep `houseLand, houseLandParking, buildingOnly, rentalHousing, rentalHousingParking`.

**Building type (建物型態 → `buildingType`)**
`住宅大樓(11層含以上有電梯)`→`residentialTower` · `華廈(10層含以下有電梯)`→`elevatorBuildingLowRise` ·
`公寓(5樓含以下無電梯)`→`walkUpApartment` · `透天厝`→`townhouse` · `店面(店鋪)`→`shopfront` ·
`辦公商業大樓`→`officeCommercialBuilding` · `廠辦`→`factoryOffice` · `工廠`→`factory` ·
`倉庫`→`warehouse` · `農舍`→`farmhouse` · `其他`→`other`.

**Parking type (車位類別 → `parkingType`)**
`坡道平面`→`rampPlane` · `坡道機械`→`rampMechanical` · `一樓平面`→`firstFloorPlane` ·
`升降機械`→`liftMechanical` · `升降平面`→`liftPlane` · `塔式車位`→`tower` · `其他`→`other`.

**Main use (主要用途 → `mainUse`, 239 raw values)** — common terms translated, rest → `other`, raw kept in `mainUseZh`:
`住家用`→`residential` · `集合住宅`→`collectiveHousing` · `國民住宅`→`publicHousing` ·
`住商用`→`residentialCommercial` · `商業用`→`commercial` · `辦公用`/`辦公室`→`office` ·
`工業用`→`industrial` · `農業用`→`agricultural` · `停車空間`→`parkingSpace` · `店鋪`→`shop` ·
`見其(他/它)登記事項`→`seeRegistrationNotes`.

**Main material (主要建材 → `mainMaterial`, 91 raw values)** — common terms translated, rest → `other`, raw in `mainMaterialZh`:
`鋼筋混凝土造/構造`→`reinforcedConcrete` · `鋼骨鋼筋混凝土造`→`steelReinforcedConcrete` · `鋼骨造`→`steel` ·
`加強磚造`→`reinforcedBrick` · `鋼筋混凝土加強磚造`→`reinforcedConcreteBrick` · `磚造`→`brick` · `木造`→`wood`.

**Booleans** `有`→1, `無`→0, blank→null.

## Geographic hierarchy

`districts` → `cities` (21, file codes a–x) → `regions` (5: Northern/Central/Southern/Eastern/Outlying
Islands, per the 主計總處 grouping with Yilan in the North) → national. Each level stores a centroid and a
WKT geometry (districts = point, cities/regions = dissolved polygon). The **region** tier still exists in
the schema (and the monthly series), but the web map now drills **city → district** only — the region map
level was removed.

District English names (`districts.nameEn`) are romanised from the Mandarin via Hanyu Pinyin (`pypinyin`)
with overrides for established spellings (e.g. 淡水→Tamsui, 鹿港→Lukang; directional 東/西/南/北/中 →
East/West/South/North/Central) — see `dataPipeline/districtNames.py`.

## Tags (`tags` / `houseTags`)

Parking: `hasParking`, `noParking`, `parkingType:{rampPlane,rampMechanical,firstFloorPlane,liftMechanical,liftPlane,tower,other}`.
Management: `hasManagementOrg`, `noManagementOrg`, `hasElevator`, `noElevator`.
New categories are added by extending `dataPipeline/tagRules.py`.

## Data cleaning & attrition (raw → clean)

Every step below removes only what it should; nothing silently deletes a city's real activity. Counts are
for **sales** over the full history (101S3–115S2 / 2012 Q3 – 2026 Q2).

| # | Step | What it does | Rows in | Rows out | Dropped |
|---|------|--------------|--------:|---------:|--------:|
| 1 | Raw load | Read every season's sale main-file (`*_lvr_land_a.csv`); parse ROC dates, Chinese-numeral floors, two header rows; `quoting=QUOTE_NONE` for the unbalanced quotes | — | 4,825,329 | — |
| 2 | De-duplicate | The same deal re-appears across overlapping quarterly releases; keep one per serial number (`編號`) | 4,825,329 | 4,810,276 | 15,053 (0.3%) |
| 3 | Housing filter | Drop **land-only** (土地) and **parking-only** (車位) transactions; keep building/house sales | 4,810,276 | 3,493,822 | 1,316,454 (27%) |
| 4 | Date sanity | Drop missing/impossible dates; keep 2012 → present | 3,493,822 | ~3,485,000 | ~8k (0.2%) |
| 5 | Price sanity *(model only)* | Drop unit price < NT$5,000 or > 3,000,000 /m² (data-entry errors) | ~3,485,000 | ~3,470,000 | ~5,600 (0.16%) |

So the cleaning removes **one big, deliberate slice** (land/parking, 27%) and then <0.5% of genuine
data-quality junk. A city's monthly sale volume is essentially untouched.

**Nothing is sampled — the whole site is the full cleaned dataset.**
- **Choropleth colours + "Current selection" stats** read the precomputed per-area full-data medians and
  counts (`cityAggregates`/`districtAggregates`). Counts are the real totals (e.g. Taipei 285,375, not a sample).
- **Time-series chart** uses `monthlyMarketSeries.json` — true monthly counts and medians. New Taipei genuinely
  has **~2,000–2,900 sales every month** through 2025.
- **Individual dots + records table** load a district's **entire** record set on drill-in (per-district gzipped
  files, up to 100k+), decompressed in-browser — so the dots, table, median, IQR and 95% CI are computed from
  every transaction. Only the district you click is fetched, so the initial page stays light.

**Not dropped, only flagged** (so you can include or exclude them): related-party deals 200,464, cancelled
deals 80, deals with an out-of-registry addition 360,086. The predictor trains on **arm's-length deals only**
(drops related-party); the explorer keeps them and lets you filter.

**Disclosure lag:** LVR reveals transactions in batches, so the newest months undercount. Loaders/exporters
treat any month below 50% of recent-stable median volume as "not yet complete" and exclude it from "latest".

## Shipped data products (one canonical layer, read many ways)

The 2.8 GB SQLite is a **local, regenerable build artifact** (never shipped). Everything below is
derived from it and is what actually ships / is consumed. Three audiences read the *same* files:
**students** (time series), the **valuation product**, and the **construction firm** (via the local
`developmentValuation.py` CLI). No duplicated datasets — differentiation is at read-time.

**`webApp/dataFiles/` (static site, ~56 MB total — 19 MB browse-sample SQLite + ~35 MB per-city
drill-down records that load *lazily*, one city at a time, so the initial page transfer stays small):**
- `summary.json` — hierarchy lists, per-type totals, transaction period, CPI table, field completeness,
  Moran's I, hedonic terms, disclosure note.
- `cityAggregates` / `districtAggregates.geojson` — geometry + per-type
  {count, median unit price, median total, median ping} for the map (city + district; the region layer
  was removed from the app).
- `districtRecords/<districtId>.json.gz` — every district's **complete** sale records, compact positional
  rows + gzipped, fetched lazily on drill-in (no sampling). `lat`/`lon` present for geocoded metros.
- `monthlyMarketSeries.json` — per city/region/national monthly {months, count, medUnitPrice}.
- **`marketSeriesMonthly.csv`** — tidy long-format series (the students' one-line load):
  `level, key, name, txnType, year, month, ym, count, unitPriceNominal, unitPriceReal2021`. Real =
  nominal × CPI₍2021₎ / CPI₍year₎ (DGBAS). **Prices are medians; recent months undercount (disclosure lag).**
- `taiwanHousing.sqlite` — ~20 MB *sample* (40k sale-housing rows + reference tables) for the in-browser
  Browse page; NOT the full DB.
- `predictor.json` — the client-side price model (see model card / below).

**`modeling/data/sales.parquet` (~80 MB, the analytical warehouse):** one row per de-duplicated housing
**sale**, columns: `saleYear, saleMonth, cityCode, cityEn, districtZh, districtEn, roadKey, unitPricePerM2,
totalPrice, livingAreaPing, landAreaPing, mainBuildingRatio, buildingAgeYears, transferFloor, totalFloors,
bedrooms, livingRooms, bathrooms, hasCompartments, hasManagementOrg, hasParking, hasElevator, buildingType,
mainUse, mainMaterial, relatedPartyDeal, hasAddition`. `roadKey` = `districtEn|road/段` (offline sub-district
geocode from the address). Query with pandas/DuckDB — this is the compact stand-in for the 2.8 GB DB.
*Note: this is the modelling dataset — plausible-year filtered; not "raw".* `presale.parquet` is the same for pre-sales.

**`predictor.json`** — `version{builtOn,dataThrough,trainRows,model}`, the serialised gradient-boost trees +
target-encoders + imputation defaults, the global conformal quantiles, and the `sigma` block (difficulty-model
trees + encoders + normalized quantiles) for the locally-weighted per-property band. See `modelCard.md`.
