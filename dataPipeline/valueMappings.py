"""Bilingual value maps: file-code -> city/region, and Mandarin enum -> English.

Clean, fully-enumerated fields (transaction target, building type, parking type,
有/無 booleans) get complete maps. High-cardinality free-text fields (main use,
main material) translate their common values and fall through to ``"other"`` while
the loader preserves the raw Mandarin in a parallel ``*Zh`` column.
"""

from __future__ import annotations

from typing import Optional

# --- Macro regions (主計總處 grouping; Yilan counted as North) -----------------
REGIONS = {
    "north":   ("Northern Taiwan", "北部"),
    "central": ("Central Taiwan", "中部"),
    "south":   ("Southern Taiwan", "南部"),
    "east":    ("Eastern Taiwan", "東部"),
    "islands": ("Outlying Islands", "離島"),
}

# --- file code -> (English name, Mandarin name, region key) --------------------
CITY_BY_CODE = {
    "a": ("Taipei City", "臺北市", "north"),
    "b": ("Taichung City", "臺中市", "central"),
    "c": ("Keelung City", "基隆市", "north"),
    "d": ("Tainan City", "臺南市", "south"),
    "e": ("Kaohsiung City", "高雄市", "south"),
    "f": ("New Taipei City", "新北市", "north"),
    "g": ("Yilan County", "宜蘭縣", "north"),
    "h": ("Taoyuan City", "桃園市", "north"),
    "i": ("Chiayi City", "嘉義市", "south"),
    "j": ("Hsinchu County", "新竹縣", "north"),
    "k": ("Miaoli County", "苗栗縣", "central"),
    "m": ("Nantou County", "南投縣", "central"),
    "n": ("Changhua County", "彰化縣", "central"),
    "o": ("Hsinchu City", "新竹市", "north"),
    "p": ("Yunlin County", "雲林縣", "central"),
    "q": ("Chiayi County", "嘉義縣", "south"),
    "t": ("Pingtung County", "屏東縣", "south"),
    "u": ("Hualien County", "花蓮縣", "east"),
    "v": ("Taitung County", "臺東縣", "east"),
    "w": ("Kinmen County", "金門縣", "islands"),
    "x": ("Penghu County", "澎湖縣", "islands"),
}

# --- 交易標的 transaction target (sale/presale + rental variants) -------------
TARGET_TYPE = {
    "房地(土地+建物)+車位": "houseLandParking",
    "房地(土地+建物)": "houseLand",
    "土地": "landOnly",
    "建物": "buildingOnly",
    "車位": "parkingOnly",
    "租賃房屋+車位": "rentalHousingParking",
    "租賃房屋": "rentalHousing",
}

# Target types that include a dwelling (used for "housing" market views).
HOUSING_TARGETS = ("houseLand", "houseLandParking", "buildingOnly",
                   "rentalHousing", "rentalHousingParking")
# Target types that include a parking space.
PARKING_TARGETS = ("houseLandParking", "parkingOnly", "rentalHousingParking")

# --- 建物型態 building type ----------------------------------------------------
BUILDING_TYPE = {
    "住宅大樓(11層含以上有電梯)": "residentialTower",
    "華廈(10層含以下有電梯)": "elevatorBuildingLowRise",
    "公寓(5樓含以下無電梯)": "walkUpApartment",
    "透天厝": "townhouse",
    "店面(店鋪)": "shopfront",
    "辦公商業大樓": "officeCommercialBuilding",
    "廠辦": "factoryOffice",
    "工廠": "factory",
    "倉庫": "warehouse",
    "農舍": "farmhouse",
    "其他": "other",
}

# --- 車位類別 parking category -------------------------------------------------
PARKING_TYPE = {
    "坡道平面": "rampPlane",
    "坡道機械": "rampMechanical",
    "一樓平面": "firstFloorPlane",
    "升降機械": "liftMechanical",
    "升降平面": "liftPlane",
    "塔式車位": "tower",
    "其他": "other",
}

# --- 主要用途 main use (common terms; else "other", raw kept by loader) --------
MAIN_USE_COMMON = {
    "住家用": "residential",
    "集合住宅": "collectiveHousing",
    "國民住宅": "publicHousing",
    "住商用": "residentialCommercial",
    "商業用": "commercial",
    "辦公用": "office",
    "辦公室": "office",
    "工業用": "industrial",
    "農業用": "agricultural",
    "停車空間": "parkingSpace",
    "店鋪": "shop",
    "其他": "other",
    "見其他登記事項": "seeRegistrationNotes",
    "見其它登記事項": "seeRegistrationNotes",
}

# --- 主要建材 main material (common terms; else "other", raw kept by loader) ---
MAIN_MATERIAL_COMMON = {
    "鋼筋混凝土造": "reinforcedConcrete",
    "鋼筋混凝土構造": "reinforcedConcrete",
    "鋼筋混凝土": "reinforcedConcrete",
    "鋼骨鋼筋混凝土造": "steelReinforcedConcrete",
    "鋼骨混凝土造": "steelReinforcedConcrete",
    "鋼骨造": "steel",
    "加強磚造": "reinforcedBrick",
    "鋼筋混凝土加強磚造": "reinforcedConcreteBrick",
    "磚造": "brick",
    "木造": "wood",
    "見其他登記事項": "seeRegistrationNotes",
    "見其它登記事項": "seeRegistrationNotes",
}


def _norm(value: Optional[str]) -> str:
    """Normalise a Mandarin token for lookup (strip, unify 台->臺)."""
    return ("" if value is None else str(value)).strip().replace("台", "臺")


def cityInfo(fileCode: str):
    """(nameEn, nameZh, regionKey) for a file code, or None."""
    return CITY_BY_CODE.get(fileCode)


def mapBool(value: Optional[str]) -> Optional[bool]:
    """有 -> True, 無 -> False, blank/other -> None."""
    text = ("" if value is None else str(value)).strip()
    if text == "有":
        return True
    if text == "無":
        return False
    return None


def mapTargetType(value: Optional[str]) -> Optional[str]:
    return TARGET_TYPE.get(_norm(value))


def mapBuildingType(value: Optional[str]) -> Optional[str]:
    text = _norm(value)
    if not text:
        return None
    return BUILDING_TYPE.get(text, "other")


def mapParkingType(value: Optional[str]) -> Optional[str]:
    text = _norm(value)
    if not text:
        return None
    return PARKING_TYPE.get(text, "other")


def mapMainUse(value: Optional[str]) -> Optional[str]:
    text = _norm(value)
    if not text:
        return None
    return MAIN_USE_COMMON.get(text, "other")


def mapMainMaterial(value: Optional[str]) -> Optional[str]:
    text = _norm(value)
    if not text:
        return None
    return MAIN_MATERIAL_COMMON.get(text, "other")
