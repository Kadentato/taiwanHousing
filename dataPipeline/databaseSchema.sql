-- Taiwan Housing database schema (SQLite).
-- Hierarchy:  house -> district -> city/county -> region -> national.
-- Geometry is stored as WKT (EPSG:4326) so it round-trips into geopandas.
-- All identifiers use lowerCamelCase.

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS houseTags;
DROP TABLE IF EXISTS tags;
DROP TABLE IF EXISTS houseParking;
DROP TABLE IF EXISTS houseLandParcels;
DROP TABLE IF EXISTS houseBuildings;
DROP TABLE IF EXISTS houses;
DROP TABLE IF EXISTS districts;
DROP TABLE IF EXISTS cities;
DROP TABLE IF EXISTS regions;

-- ---------------------------------------------------------------- hierarchy ---
CREATE TABLE regions (
    regionId    INTEGER PRIMARY KEY,
    regionKey   TEXT UNIQUE NOT NULL,   -- north | central | south | east | islands
    nameEn      TEXT NOT NULL,
    nameZh      TEXT NOT NULL,
    geometryWkt TEXT                     -- dissolved member-county polygon
);

CREATE TABLE cities (
    cityId      INTEGER PRIMARY KEY,
    fileCode    TEXT UNIQUE NOT NULL,    -- source file prefix a..x
    nameEn      TEXT NOT NULL,
    nameZh      TEXT NOT NULL,
    regionId    INTEGER NOT NULL REFERENCES regions(regionId),
    centroidLat REAL,
    centroidLon REAL,
    geometryWkt TEXT                      -- dissolved township polygon
);

CREATE TABLE districts (
    districtId     INTEGER PRIMARY KEY,
    cityId         INTEGER NOT NULL REFERENCES cities(cityId),
    nameZh         TEXT NOT NULL,
    nameEn         TEXT,                  -- romanised (pypinyin + overrides), see districtNames.py
    centroidLat    REAL,
    centroidLon    REAL,
    centroidSource TEXT,                  -- townExact | townCore | cityFallback | missing
    geometryWkt    TEXT,                  -- centroid Point
    UNIQUE (cityId, nameZh)
);

-- -------------------------------------------------------------- fact table ----
CREATE TABLE houses (
    houseId          INTEGER PRIMARY KEY,
    serialNumber     TEXT,               -- 編號 (source join key)
    sourceFile       TEXT,
    cityId           INTEGER NOT NULL REFERENCES cities(cityId),
    districtId       INTEGER NOT NULL REFERENCES districts(districtId),

    transactionType  TEXT NOT NULL,      -- sale | presale | rental
    targetType       TEXT,               -- houseLand | houseLandParking | landOnly | parkingOnly | buildingOnly
    address          TEXT,

    saleDate         TEXT,               -- ISO yyyy-mm-dd
    saleYear         INTEGER,
    saleMonth        INTEGER,
    saleQuarter      INTEGER,

    totalPrice       INTEGER,            -- NTD (總價元 / 總額元)
    unitPricePerM2   REAL,               -- NTD per m^2
    parkingPrice     INTEGER,

    buildingAreaM2   REAL,
    landAreaM2       REAL,
    buildingAreaPing REAL,
    landAreaPing     REAL,
    livingAreaM2     REAL,               -- building area net of parking footprint
    livingAreaPing   REAL,
    mainBuildingAreaM2 REAL,
    auxBuildingAreaM2  REAL,
    balconyAreaM2      REAL,

    bedrooms         INTEGER,
    livingRooms      INTEGER,
    bathrooms        INTEGER,
    hasCompartments  INTEGER,            -- 0/1

    totalFloors      INTEGER,
    transferFloor    INTEGER,            -- parsed primary floor; null if multi/complex
    buildingType     TEXT,
    mainUse          TEXT,               -- translated (common term else 'other')
    mainUseZh        TEXT,               -- raw Mandarin preserved
    mainMaterial     TEXT,
    mainMaterialZh   TEXT,
    buildCompletionDate TEXT,            -- ISO
    buildingAgeYears REAL,

    hasManagementOrg INTEGER,            -- 0/1
    hasElevator      INTEGER,            -- 0/1
    hasParking       INTEGER,            -- 0/1
    parkingType      TEXT,
    parkingAreaM2    REAL,
    hasFurniture     INTEGER,            -- rental only, 0/1

    rentalType       TEXT,               -- rental only
    rentalStartDate  TEXT,               -- rental only, ISO
    rentalEndDate    TEXT,               -- rental only, ISO
    rentalServices   TEXT,               -- rental only

    projectName      TEXT,               -- presale only (建案名稱)
    terminationStatus TEXT,              -- presale only (解約情形, raw)

    centroidLat      REAL,
    centroidLon      REAL,
    geometryWkt      TEXT,               -- Point(lon lat), EPSG:4326

    -- optional enrichment (populated only by the future networked step)
    latitude         REAL,
    longitude        REAL,
    nearestSchoolName       TEXT,
    nearestSchoolDistanceM  REAL,
    schoolsWithin1km        INTEGER,

    note             TEXT,               -- 備註 verbatim

    -- deal-quality flags parsed from note / termination status
    relatedPartyDeal INTEGER,            -- 0/1: 親友/特殊關係/二親等/關係人 (non-arm's-length)
    cancelledDeal    INTEGER,            -- 0/1: 解約 (contract later cancelled)
    hasAddition      INTEGER             -- 0/1: 增建 (unpermitted addition noted)
);

-- ------------------------------------------------------------- sub-tables -----
CREATE TABLE houseBuildings (        -- *_build.csv
    id             INTEGER PRIMARY KEY,
    houseId        INTEGER REFERENCES houses(houseId),
    serialNumber   TEXT,
    ageYears       REAL,             -- 屋齡
    areaM2         REAL,             -- 建物移轉面積平方公尺
    mainUse        TEXT,
    mainUseZh      TEXT,
    material       TEXT,
    materialZh     TEXT,
    completionDate TEXT,             -- ISO
    totalFloors    INTEGER,          -- 總層數
    buildingLayer  TEXT,             -- 建物分層 (raw)
    transferStatus TEXT              -- 移轉情形 (raw)
);

CREATE TABLE houseLandParcels (      -- *_land.csv
    id               INTEGER PRIMARY KEY,
    houseId          INTEGER REFERENCES houses(houseId),
    serialNumber     TEXT,
    landPosition     TEXT,           -- 土地位置 (raw)
    areaM2           REAL,           -- 土地移轉面積平方公尺
    zoning           TEXT,           -- 使用分區或編定 (raw)
    shareNumerator   INTEGER,        -- 權利人持分分子
    shareDenominator INTEGER,        -- 權利人持分分母
    transferStatus   TEXT,           -- 移轉情形 (raw)
    parcelNumber     TEXT            -- 地號
);

CREATE TABLE houseParking (          -- *_park.csv
    id           INTEGER PRIMARY KEY,
    houseId      INTEGER REFERENCES houses(houseId),
    serialNumber TEXT,
    parkingType  TEXT,
    parkingTypeZh TEXT,
    price        INTEGER,            -- 車位價格
    areaM2       REAL,               -- 車位面積平方公尺
    floor        TEXT                -- 車位所在樓層 (raw)
);

-- -------------------------------------------------------------- tag system ----
CREATE TABLE tags (
    tagId    INTEGER PRIMARY KEY,
    slug     TEXT UNIQUE NOT NULL,
    labelEn  TEXT NOT NULL,
    labelZh  TEXT,
    category TEXT NOT NULL
);

CREATE TABLE houseTags (
    houseId INTEGER NOT NULL REFERENCES houses(houseId),
    tagId   INTEGER NOT NULL REFERENCES tags(tagId),
    PRIMARY KEY (houseId, tagId)
);

-- ----------------------------------------------------------------- indexes ----
CREATE INDEX idxHousesCity        ON houses(cityId);
CREATE INDEX idxHousesDistrict    ON houses(districtId);
CREATE INDEX idxHousesTxnType     ON houses(transactionType);
CREATE INDEX idxHousesYearMonth   ON houses(saleYear, saleMonth);
CREATE INDEX idxHousesTarget      ON houses(targetType);
CREATE INDEX idxHouseTagsTag      ON houseTags(tagId);
CREATE INDEX idxBuildingsHouse    ON houseBuildings(houseId);
CREATE INDEX idxLandHouse         ON houseLandParcels(houseId);
CREATE INDEX idxParkingHouse      ON houseParking(houseId);
