"""Load the LVR CSVs into the SQLite database.

For each (city, transaction-type) it reads the main file (skipping the second,
English, header row), translates/normalises every field to the camelCase English
schema, inserts a ``houses`` row, then attaches the build / land / park
sub-tables by the ``編號`` serial number. Areas are converted to ping and the
geographic hierarchy (region/city/district) is seeded and resolved as it goes.
"""

from __future__ import annotations

import csv
import datetime
import os
import sqlite3
from typing import Optional

import pandas as pd

from . import valueMappings as vm
from .chineseNumeralParser import parseFloorCount
from .dealFlags import dealFlags
from .districtNames import toEnglish
from .geoLookup import GeoLookup
from .rocDateParser import buildingAgeYears, parseRocDate, parseRocDateRange

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(HERE, "databaseSchema.sql")
PING_PER_M2 = 1.0 / 3.305785            # 1 m^2 = 0.3025 ping
TXN_TYPE = {"a": "sale", "b": "presale", "c": "rental"}
# Plausible transaction-year range; historical files contain a few data-entry
# errors (e.g. ROC 10 -> 1921, ROC 117 -> 2028) that would otherwise pollute the
# year filter. Registration began 2012, so anything wildly off is treated as missing.
MIN_TXN_YEAR = 2000
MAX_TXN_YEAR = datetime.date.today().year + 1


# ------------------------------------------------------------- value helpers ---
def _clean(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def toInt(value) -> Optional[int]:
    text = _clean(value).replace(",", "")
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def toFloat(value) -> Optional[float]:
    text = _clean(value).replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def toPing(m2: Optional[float]) -> Optional[float]:
    return None if m2 is None else round(m2 * PING_PER_M2, 4)


def boolInt(value) -> Optional[int]:
    b = vm.mapBool(value)
    return None if b is None else int(b)


# ----------------------------------------------------------------- main load ---
class Loader:
    def __init__(self, conn: sqlite3.Connection, sourceDir: str, geo: GeoLookup):
        self.conn = conn
        self.sourceDir = sourceDir
        self.geo = geo
        self.regionId: dict = {}
        self.cityId: dict = {}            # fileCode -> cityId
        self.districtId: dict = {}        # (cityId, nameZh) -> districtId
        self.seen: set = set()            # 編號 already loaded (cross-season dedup)
        self.season = None                # current season label, for provenance
        self._houseCount = 0              # running houseId high-water mark (for batched inserts)
        self.loadSubTables = True         # build/land/park subs (unused by the web export)
        self.salesOnly = False            # only load sale (_a) files, not presale/rental

    # -- schema + hierarchy seed ------------------------------------------------
    def createSchema(self):
        with open(SCHEMA_PATH, encoding="utf-8") as fh:
            self.conn.executescript(fh.read())

    def seedHierarchy(self):
        cur = self.conn.cursor()
        for key, (en, zh) in vm.REGIONS.items():
            cur.execute(
                "INSERT INTO regions(regionKey, nameEn, nameZh) VALUES (?,?,?)",
                (key, en, zh),
            )
            self.regionId[key] = cur.lastrowid
        for code, (en, zh, regionKey) in vm.CITY_BY_CODE.items():
            centroid = self.geo.cityCentroid(code)
            lat, lon = (centroid if centroid else (None, None))
            cur.execute(
                "INSERT INTO cities(fileCode, nameEn, nameZh, regionId, centroidLat, centroidLon)"
                " VALUES (?,?,?,?,?,?)",
                (code, en, zh, self.regionId[regionKey], lat, lon),
            )
            self.cityId[code] = cur.lastrowid
        self.conn.commit()

    def _districtFor(self, code: str, nameZh: str) -> int:
        cityId = self.cityId[code]
        key = (cityId, nameZh)
        if key in self.districtId:
            return self.districtId[key]
        lat, lon, source = self.geo.resolve(code, nameZh)
        wkt = f"POINT ({lon} {lat})" if lat is not None else None
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO districts(cityId, nameZh, nameEn, centroidLat, centroidLon, centroidSource, geometryWkt)"
            " VALUES (?,?,?,?,?,?,?)",
            (cityId, nameZh, toEnglish(nameZh), lat, lon, source, wkt),
        )
        self.districtId[key] = cur.lastrowid
        return cur.lastrowid

    # -- per-file load ----------------------------------------------------------
    def _readCsv(self, name: str) -> Optional[pd.DataFrame]:
        path = os.path.join(self.sourceDir, name)
        if not os.path.exists(path):
            return None
        # Older historical releases contain a few malformed rows: stray commas
        # (skipped) and unbalanced quotes (QUOTE_NONE stops one bad quote from
        # swallowing the rest of the file with "EOF inside string"). LVR fields
        # aren't CSV-quoted, so treating " literally is safe.
        return pd.read_csv(path, skiprows=[1], dtype=str, keep_default_na=False,
                           encoding="utf-8-sig", on_bad_lines="skip", quoting=csv.QUOTE_NONE)

    def loadAll(self) -> dict:
        """Load a single release from self.sourceDir."""
        return self._loadDir()

    def loadSeasonDir(self, dirPath: str, season: str = None) -> dict:
        """Load one season folder (used by the multi-season build)."""
        self.sourceDir = dirPath
        self.season = season
        return self._loadDir()

    def _loadDir(self) -> dict:
        stats = {"sale": 0, "presale": 0, "rental": 0, "build": 0, "land": 0, "park": 0}
        suffixes = ("a",) if self.salesOnly else ("a", "b", "c")
        for code in vm.CITY_BY_CODE:
            for suffix in suffixes:
                for k, v in self.loadFile(code, suffix).items():
                    stats[k] += v
        return stats

    def loadFile(self, code: str, suffix: str) -> dict:
        """Load one (city, transaction-type) main file plus its sub-tables.

        A 編號 already loaded (from a newer season, since we ingest newest-first)
        is skipped, so each transaction is counted once with its latest disclosure.
        """
        stats = {"sale": 0, "presale": 0, "rental": 0, "build": 0, "land": 0, "park": 0}
        df = self._readCsv(f"{code}_lvr_land_{suffix}.csv")
        if df is None:
            return stats
        txn = TXN_TYPE[suffix]

        records, serials = [], []
        for row in df.to_dict("records"):     # faster than iterrows, same .get access
            serial = _clean(row.get("編號"))
            if serial and serial in self.seen:
                continue                       # a newer season already has this transaction
            records.append(self._houseRecord(code, suffix, txn, row))
            serials.append(serial)
            if serial:
                self.seen.add(serial)

        serialToHouse: dict = {}              # only serials inserted from THIS file
        if records:
            cols = list(records[0])
            sql = f"INSERT INTO houses({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
            self.conn.executemany(sql, [tuple(r[c] for c in cols) for r in records])
            firstId = self._houseCount + 1    # houseId rowids are assigned sequentially
            self._houseCount += len(records)
            for i, serial in enumerate(serials):
                if serial:
                    serialToHouse[serial] = firstId + i
            stats[txn] = len(records)

        if self.loadSubTables:
            stats["build"] += self._loadBuild(code, suffix, serialToHouse)
            stats["land"] += self._loadLand(code, suffix, serialToHouse)
            stats["park"] += self._loadPark(code, suffix, serialToHouse)
        self.conn.commit()
        return stats

    def _houseRecord(self, code: str, suffix: str, txn: str, row) -> dict:
        g = row.get
        districtZh = _clean(g("鄉鎮市區"))
        districtId = self._districtFor(code, districtZh)

        # Column names differ slightly between sale/presale and rental.
        isRent = suffix == "c"
        saleDate = parseRocDate(g("租賃年月日") if isRent else g("交易年月日"))
        landArea = toFloat(g("土地面積平方公尺") if isRent else g("土地移轉總面積平方公尺"))
        buildArea = toFloat(g("建物總面積平方公尺") if isRent else g("建物移轉總面積平方公尺"))
        totalPrice = toInt(g("總額元") if isRent else g("總價元"))
        parkArea = toFloat(g("車位面積平方公尺") if isRent else g("車位移轉總面積平方公尺"))
        parkPrice = toInt(g("車位總額元") if isRent else g("車位總價元"))
        transferFloorRaw = g("租賃層次") if isRent else g("移轉層次")

        completion = parseRocDate(g("建築完成年月"))
        parkingTypeZh = _clean(g("車位類別"))
        targetType = vm.mapTargetType(g("交易標的"))
        hasParking = int(
            bool(parkingTypeZh)
            or (parkArea or 0) > 0
            or (parkPrice or 0) > 0
            or targetType in vm.PARKING_TARGETS
        )

        # Elevator: sale uses 電梯, rental uses 有無電梯, presale has neither.
        elevator = boolInt(g("有無電梯")) if isRent else boolInt(g("電梯"))

        rentStart = rentEnd = None
        if isRent:
            rentStart, rentEnd = parseRocDateRange(g("租賃期間"))

        year = int(saleDate[:4]) if saleDate else None
        if year is not None and not (MIN_TXN_YEAR <= year <= MAX_TXN_YEAR):
            saleDate = year = None                      # implausible date -> treat as missing
        month = int(saleDate[5:7]) if saleDate else None
        quarter = ((month - 1) // 3 + 1) if month else None

        # Building area includes the parking footprint; net it out for a cleaner size.
        livingArea = None
        if buildArea is not None:
            livingArea = round(max(buildArea - (parkArea or 0), 0.0), 2)

        note = _clean(g("備註"))
        related, cancelled, addition = dealFlags(note, _clean(g("解約情形")))

        mainUseZh = _clean(g("主要用途"))
        materialZh = _clean(g("主要建材"))
        lat, lon, _src = self.geo.resolve(code, districtZh)
        wkt = f"POINT ({lon} {lat})" if lat is not None else None

        record = {
            "serialNumber": _clean(g("編號")),
            "sourceFile": (f"{self.season}/" if self.season else "") + f"{code}_lvr_land_{suffix}.csv",
            "cityId": self.cityId[code],
            "districtId": districtId,
            "transactionType": txn,
            "targetType": targetType,
            "address": _clean(g("土地位置建物門牌")),
            "saleDate": saleDate,
            "saleYear": year,
            "saleMonth": month,
            "saleQuarter": quarter,
            "totalPrice": totalPrice,
            "unitPricePerM2": toFloat(g("單價元平方公尺")),
            "parkingPrice": parkPrice,
            "buildingAreaM2": buildArea,
            "landAreaM2": landArea,
            "buildingAreaPing": toPing(buildArea),
            "landAreaPing": toPing(landArea),
            "livingAreaM2": livingArea,
            "livingAreaPing": toPing(livingArea),
            "mainBuildingAreaM2": toFloat(g("主建物面積")),
            "auxBuildingAreaM2": toFloat(g("附屬建物面積")),
            "balconyAreaM2": toFloat(g("陽台面積")),
            "bedrooms": toInt(g("建物現況格局-房")),
            "livingRooms": toInt(g("建物現況格局-廳")),
            "bathrooms": toInt(g("建物現況格局-衛")),
            "hasCompartments": boolInt(g("建物現況格局-隔間")),
            "totalFloors": parseFloorCount(g("總樓層數")),
            "transferFloor": parseFloorCount(transferFloorRaw),
            "buildingType": vm.mapBuildingType(g("建物型態")),
            "mainUse": vm.mapMainUse(mainUseZh),
            "mainUseZh": mainUseZh or None,
            "mainMaterial": vm.mapMainMaterial(materialZh),
            "mainMaterialZh": materialZh or None,
            "buildCompletionDate": completion,
            "buildingAgeYears": buildingAgeYears(completion, saleDate),
            "hasManagementOrg": boolInt(g("有無管理組織")),
            "hasElevator": elevator,
            "hasParking": hasParking,
            "parkingType": vm.mapParkingType(parkingTypeZh),
            "parkingAreaM2": parkArea,
            "hasFurniture": boolInt(g("有無附傢俱")) if isRent else None,
            "rentalType": _clean(g("出租型態")) or None if isRent else None,
            "rentalStartDate": rentStart,
            "rentalEndDate": rentEnd,
            "rentalServices": _clean(g("租賃住宅服務")) or None if isRent else None,
            "projectName": _clean(g("建案名稱")) or None,
            "terminationStatus": _clean(g("解約情形")) or None,
            "centroidLat": lat,
            "centroidLon": lon,
            "geometryWkt": wkt,
            "note": note or None,
            "relatedPartyDeal": related,
            "cancelledDeal": cancelled,
            "hasAddition": addition,
        }
        return record

    # -- sub-tables -------------------------------------------------------------
    def _loadBuild(self, code, suffix, serialToHouse) -> int:
        df = self._readCsv(f"{code}_lvr_land_{suffix}_build.csv")
        if df is None:
            return 0
        rows = []
        for r in df.to_dict("records"):
            serial = _clean(r.get("編號"))
            houseId = serialToHouse.get(serial)
            if houseId is None:        # house came from another season (deduped) — skip its subs
                continue
            useZh = _clean(r.get("主要用途"))
            matZh = _clean(r.get("主要建材"))
            rows.append((
                houseId, serial, toFloat(r.get("屋齡")), toFloat(r.get("建物移轉面積平方公尺")),
                vm.mapMainUse(useZh), useZh or None, vm.mapMainMaterial(matZh), matZh or None,
                parseRocDate(r.get("建築完成日期")), parseFloorCount(r.get("總層數")),
                _clean(r.get("建物分層")) or None, _clean(r.get("移轉情形")) or None,
            ))
        self.conn.executemany(
            "INSERT INTO houseBuildings(houseId, serialNumber, ageYears, areaM2, mainUse, mainUseZh,"
            " material, materialZh, completionDate, totalFloors, buildingLayer, transferStatus)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows,
        )
        return len(rows)

    def _loadLand(self, code, suffix, serialToHouse) -> int:
        df = self._readCsv(f"{code}_lvr_land_{suffix}_land.csv")
        if df is None:
            return 0
        rows = []
        for r in df.to_dict("records"):
            serial = _clean(r.get("編號"))
            houseId = serialToHouse.get(serial)
            if houseId is None:
                continue
            rows.append((
                houseId, serial, _clean(r.get("土地位置")) or None,
                toFloat(r.get("土地移轉面積平方公尺")), _clean(r.get("使用分區或編定")) or None,
                toInt(r.get("權利人持分分子")), toInt(r.get("權利人持分分母")),
                _clean(r.get("移轉情形")) or None, _clean(r.get("地號")) or None,
            ))
        self.conn.executemany(
            "INSERT INTO houseLandParcels(houseId, serialNumber, landPosition, areaM2, zoning,"
            " shareNumerator, shareDenominator, transferStatus, parcelNumber)"
            " VALUES (?,?,?,?,?,?,?,?,?)", rows,
        )
        return len(rows)

    def _loadPark(self, code, suffix, serialToHouse) -> int:
        df = self._readCsv(f"{code}_lvr_land_{suffix}_park.csv")
        if df is None:
            return 0
        rows = []
        for r in df.to_dict("records"):
            serial = _clean(r.get("編號"))
            houseId = serialToHouse.get(serial)
            if houseId is None:
                continue
            typeZh = _clean(r.get("車位類別"))
            rows.append((
                houseId, serial, vm.mapParkingType(typeZh), typeZh or None,
                toInt(r.get("車位價格")), toFloat(r.get("車位面積平方公尺")),
                _clean(r.get("車位所在樓層")) or None,
            ))
        self.conn.executemany(
            "INSERT INTO houseParking(houseId, serialNumber, parkingType, parkingTypeZh, price, areaM2, floor)"
            " VALUES (?,?,?,?,?,?,?)", rows,
        )
        return len(rows)
