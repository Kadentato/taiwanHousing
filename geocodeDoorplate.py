"""Geocode LVR housing sales to real coordinates using the government 門牌坐標
(doorplate address-point) open data, then rewrite the web app's per-city record
files with lat/lon so the map plots houses at their actual addresses.

Best-effort, per county: any county with a compatible doorplate dataset gets real
coordinates; counties without one keep the client-side jitter fallback. Source files
(large) are cached under geoReference/doorplate/ (gitignored) and re-downloadable.

    python geocodeDoorplate.py            # all configured counties
    python geocodeDoorplate.py a f        # just these city codes
"""
from __future__ import annotations

import json
import os
import re
import ssl
import sys
import urllib.request

import geopandas as gpd
import pandas as pd
from pyproj import Transformer
from shapely.geometry import Point

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from dataPipeline.webDataExporter import HOUSING_TARGETS, MAX_RECORDS_PER_CITY  # noqa: E402

DB = os.path.join(HERE, "database", "taiwanHousing.sqlite")
CACHE = os.path.join(HERE, "geoReference", "doorplate")
DATAFILES = os.path.join(HERE, "webApp", "dataFiles")
FW = str.maketrans("０１２３４５６７８９", "0123456789")

# Per-county config: data.gov.tw dataset id + the doorplate CSV's column names.
# All share the same 門牌坐標 shape (district-code, road+段, 巷, 弄, 號, TWD97 x/y);
# only the header names differ. Add a county by appending an entry.
COUNTY = {
    "a": {"id": "155472",  # 臺北市
          "cols": {"code": "鄉鎮市區代碼", "road": "街路段", "lane": "巷", "alley": "弄",
                   "num": "號", "x": "橫座標", "y": "縱座標"}},
    "f": {"id": "168887",  # 新北市
          "cols": {"code": "areacode", "road": "street、road、section", "lane": "lane",
                   "alley": "alley", "num": "number", "x": "x_3826", "y": "y_3826"}},
    "b": {"gdrive": "1Nl4xNrD2zxZSzzZAUUDZA31Ov8a72Q6P",  # 臺中市 (Google-Drive zip, WGS84 lat/lon)
          "cols": {"code": "鄉鎮市區代碼", "road": "街_路段", "lane": "巷", "alley": "弄",
                   "num": "號", "lat": "WGS84緯度", "lon": "WGS84經度"}},
    "h": {"tycg": "ec47dbd5-9ed8-4c8d-8ce1-ccb63b1b72e6",  # 桃園市 (Taoyuan portal, monthly TWD97 snapshots)
          "cols": {"code": "鄉鎮市區代碼", "road": "街路段", "lane": "巷", "alley": "弄",
                   "num": "號", "x": "橫座標", "y": "縱座標"}},
    "d": {"url": "https://data.tainan.gov.tw/File/ResourceCsvDownload/af44f904-2f4c-49b2-aaf8-1a64dce09bd4",
          "cols": {"code": "鄉鎮市區代碼", "road": "街、路段", "lane": "巷", "alley": "弄",  # 臺南市 114年 (TWD97)
                   "num": "號", "x": "橫座標", "y": "縱座標"}},
    # 高雄市: api.kcg.gov.tw service (slug {rocYear}-kh-address; TWD97, Taipei-schema columns). As of
    # 2026-07 the platform's data-reader backend returns a server-side 403 for ALL years — run once it
    # recovers: `python geocodeDoorplate.py e` (bump {rocYear} to the latest published).
    "e": {"url": "https://api.kcg.gov.tw/api/Service/Csv/115-kh-address",
          "cols": {"code": "鄉鎮市區代碼", "road": "街路段", "lane": "巷", "alley": "弄",
                   "num": "號", "x": "橫座標", "y": "縱座標"}},
}
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

ROAD = re.compile(r"([一-鿿]{1,10}?(?:路|街|大道))([一二三四五六七八九十]+段)?")
_tok = lambda pat, s: (re.search(pat, s) or re.search(r"$^", "")) and (re.search(pat, s).group(1) if re.search(pat, s) else "")


def _get(url, n=None):
    r = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), context=_CTX, timeout=300)
    return r.read(n) if n else r.read()


def _tycgLatestCsvUrl(pid):
    """Taoyuan's portal publishes the doorplate set as one CSV resource per month
    (a full cumulative snapshot each time). Resolve the newest month's download URL."""
    info = json.loads(_get(f"https://opendata.tycg.gov.tw/api/v1/resource.info?pid={pid}"))["payload"]
    csvs = [r for r in info if (r.get("file_format") or "").upper() == "CSV"]

    def ym(r):
        m = re.search(r"\((\d+)年(\d+)月", r.get("name", ""))
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)

    rid = max(csvs, key=ym)["rid"]  # latest monthly snapshot = most complete
    return f"https://opendata.tycg.gov.tw/api/v1/dataset/{pid}/resource/{rid}/download"


def ensureCsv(code, cfg):
    path = os.path.join(CACHE, f"{code}.csv")
    if os.path.exists(path):
        return path
    os.makedirs(CACHE, exist_ok=True)
    print(f"  downloading {code} doorplate ...")
    if "gdrive" in cfg:  # Google-Drive-hosted zip of a CSV (e.g. Taichung)
        import io
        import zipfile
        raw = _get(f"https://drive.usercontent.google.com/download?id={cfg['gdrive']}&export=download&confirm=t")
        z = zipfile.ZipFile(io.BytesIO(raw))
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        open(path, "wb").write(z.read(name))
    elif "tycg" in cfg:  # Taoyuan opendata portal -> newest monthly CSV snapshot
        open(path, "wb").write(_get(_tycgLatestCsvUrl(cfg["tycg"])))
    elif "url" in cfg:  # a direct CSV download URL (e.g. a county open-data file)
        open(path, "wb").write(_get(cfg["url"]))
    else:  # data.gov.tw dataset -> direct CSV distribution
        meta = json.loads(_get(f"https://data.gov.tw/api/v2/rest/dataset/{cfg['id']}"))["result"]
        url = next(d["resourceDownloadUrl"] for d in meta["distribution"]
                   if (d.get("resourceFormat") or "").upper() == "CSV")
        open(path, "wb").write(_get(url))
    return path


def numOf(s):
    m = re.search(r"\d+", str(s).translate(FW))
    return m.group(0) if m else ""


def laneTok(s, marker):
    return re.sub(marker + "$", "", str(s)).translate(FW).strip()


def buildIndex(code, cfg, townships, currentNames=None):
    """Return (doormap, roadmap, districtNames) for a county.

    ``currentNames`` = the city's up-to-date district names from the DB. The bundled
    township polygons can carry stale names (e.g. Taoyuan's pre-2014 中壢市/大溪鎮 vs the
    current 中壢區/大溪區 the LVR addresses use), so we remap the point-in-polygon name to
    the current one by stem (strip the 鄉/鎮/市/區 suffix). Without this, every Taoyuan
    address collides on the "桃園市" city prefix and nothing joins.
    """
    c = cfg["cols"]
    dp = pd.read_csv(ensureCsv(code, cfg), dtype=str).fillna("")
    if "lat" in c:  # dataset already carries WGS84 lat/lon (e.g. Taichung)
        dp["lat"] = pd.to_numeric(dp[c["lat"]], errors="coerce")
        dp["lon"] = pd.to_numeric(dp[c["lon"]], errors="coerce")
    else:  # TWD97 (EPSG:3826) easting/northing -> reproject to WGS84
        x = pd.to_numeric(dp[c["x"]], errors="coerce")
        y = pd.to_numeric(dp[c["y"]], errors="coerce")
        m = x.notna() & y.notna()
        dp = dp[m].copy()
        lon, lat = Transformer.from_crs("EPSG:3826", "EPSG:4326", always_xy=True).transform(
            x[m].values, y[m].values)
        dp["lat"], dp["lon"] = lat, lon
    dp = dp.dropna(subset=["lat", "lon"]).copy()
    # district name per district-code via point-in-polygon (robust for odd-shaped districts)
    reps = dp.groupby(c["code"])[["lon", "lat"]].median().reset_index()
    pts = gpd.GeoDataFrame(reps, geometry=[Point(xy) for xy in zip(reps.lon, reps.lat)], crs="EPSG:4326")
    towns = townships[townships["cityCode"] == code][["town", "geometry"]]
    joined = gpd.sjoin(pts, towns, how="left", predicate="within")
    code2name = dict(zip(joined[c["code"]], joined["town"]))
    if currentNames:  # remap stale township names -> current LVR district names by stem
        stem = lambda s: re.sub(r"[鄉鎮市區]$", "", str(s))
        cur = {stem(n): n for n in currentNames}
        code2name = {k: cur.get(stem(v), v) for k, v in code2name.items()}
    dp["district"] = dp[c["code"]].map(code2name)
    dp = dp[dp["district"].notna()]
    dp["ln"] = dp[c["lane"]].map(lambda s: laneTok(s, "巷"))
    dp["al"] = dp[c["alley"]].map(lambda s: laneTok(s, "弄"))
    dp["nm"] = dp[c["num"]].map(numOf)
    dp["dk"] = dp["district"] + "|" + dp[c["road"]] + "|" + dp["ln"] + "|" + dp["al"] + "|" + dp["nm"]
    doormap = {k: (r.lat, r.lon) for k, r in dp.groupby("dk")[["lat", "lon"]].mean().iterrows()}
    roadmap = {f"{d}|{r}": (row.lat, row.lon)
               for (d, r), row in dp.groupby(["district", c["road"]])[["lat", "lon"]].mean().iterrows()}
    return doormap, roadmap, set(dp["district"].unique())


def geocode(addr, districts, doormap, roadmap):
    n = addr.translate(FW)
    dist = next((d for d in districts if d in n), None)
    if not dist:
        return (None, None)
    tail = n.split(dist, 1)[1]
    r = ROAD.search(tail)
    if not r:
        return (None, None)
    road = r.group(1) + (r.group(2) or "")
    rest = tail[r.end():]
    ln = (re.search(r"([一-鿿\d]+?)巷", rest) or None)
    al = (re.search(r"([一-鿿\d]+?)弄", rest) or None)
    nm = (re.search(r"(\d+)號", rest) or None)
    key = f"{dist}|{road}|{ln.group(1) if ln else ''}|{al.group(1) if al else ''}|{nm.group(1) if nm else ''}"
    return doormap.get(key) or roadmap.get(f"{dist}|{road}", (None, None))


KEEP = ["districtId", "transactionType", "targetType", "saleYear", "saleMonth", "totalPrice",
        "unitPricePerM2", "livingAreaPing", "bedrooms", "bathrooms", "buildingType",
        "buildingAgeYears", "hasParking", "parkingType", "hasElevator", "hasManagementOrg",
        "relatedPartyDeal", "cancelledDeal", "hasAddition"]


def main(argv):
    import sqlite3
    codes = argv or list(COUNTY)
    townships = gpd.read_file(os.path.join(HERE, "geoReference", "townshipBoundaries.geojson"))
    conn = sqlite3.connect(DB)
    for code in codes:
        if code not in COUNTY:
            print(f"[{code}] no doorplate config — skipped (keeps jitter fallback)")
            continue
        print(f"[{code}] building doorplate index ...")
        cid = conn.execute("SELECT cityId FROM cities WHERE fileCode=?", (code,)).fetchone()[0]
        currentNames = [r[0] for r in conn.execute(
            "SELECT nameZh FROM districts WHERE cityId=? AND nameZh IS NOT NULL", (cid,))]
        doormap, roadmap, districts = buildIndex(code, COUNTY[code], townships, currentNames)
        cols = ",".join(["houseId", *KEEP, "address"])
        h = pd.read_sql(f"SELECT {cols} FROM houses WHERE cityId={cid} AND transactionType='sale' "
                        "AND address IS NOT NULL", conn)
        h = h[h["targetType"].isin(HOUSING_TARGETS) & h["saleYear"].between(2012, 2026)].copy()
        g = [geocode(a, districts, doormap, roadmap) for a in h["address"]]
        h["lat"] = [x[0] for x in g]
        h["lon"] = [x[1] for x in g]
        placedAll = h["lat"].notna().mean()
        samp = h.sample(min(MAX_RECORDS_PER_CITY, len(h)), random_state=0).reset_index(drop=True)
        recs = json.loads(samp[KEEP].to_json(orient="records"))
        for rec, la, lo in zip(recs, samp["lat"].tolist(), samp["lon"].tolist()):
            rec["lat"] = round(float(la), 6) if pd.notna(la) else None
            rec["lon"] = round(float(lo), 6) if pd.notna(lo) else None
        placed = sum(1 for r in recs if r["lat"] is not None)
        json.dump(recs, open(os.path.join(DATAFILES, f"cityRecords_{code}.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)
        print(f"[{code}] {len(h):,} sales, {placedAll*100:.0f}% geocodable; "
              f"wrote {len(recs)} records ({placed/len(recs)*100:.0f}% with real coords)")
    print("Done. Bump DATA_V in appMain.js so browsers refetch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
