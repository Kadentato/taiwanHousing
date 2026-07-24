"""Fetch the Taipei Metro (MRT) network from OpenStreetMap (Overpass) and write a compact
GeoJSON overlay for the web map: one coloured line per route + station points.

    python spatialAnalysis/fetchMrt.py

Output: webApp/dataFiles/taipeiMrt.geojson (lines with official colours + stations).
Re-run to refresh; the file is small and committed so the static site needs no network.
"""
import json
import os
import ssl
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "webApp", "dataFiles", "taipeiMrt.geojson")
# Taipei + New Taipei metro area (south, west, north, east)
BBOX = "24.93,121.36,25.20,121.68"

QUERY = f"""
[out:json][timeout:180];
(
  relation["route"="subway"]({BBOX});
  node["station"="subway"]({BBOX});
  node["railway"="station"]["subway"="yes"]({BBOX});
);
out geom;
"""

# Canonical line names by OSM ref (OSM's per-relation names are direction/short-turn specific,
# e.g. "Circular Line Northbound" or "Taipower Building => Songshan").
LINE_NAMES = {
    "R": "Tamsui–Xinyi Line", "G": "Songshan–Xindian Line", "O": "Zhonghe–Xinlu Line",
    "BL": "Bannan Line", "BR": "Wenhu Line", "Y": "Circular Line",
    "A": "Airport MRT", "LB": "Sanying Line",
}
# Override colours OSM records too pale to see over the basemap (Airport MRT's commuter service).
LINE_COLORS = {"A": "#8246AF"}

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]


def overpass(query):
    data = urllib.parse.urlencode({"data": query}).encode()
    last = None
    for host in ENDPOINTS:
        try:
            print(f"  querying {host} ...")
            req = urllib.request.Request(host, data=data, headers={"User-Agent": "taiwanHousing/1.0"})
            return json.loads(urllib.request.urlopen(req, context=_CTX, timeout=200).read())
        except Exception as e:  # noqa: BLE001
            print(f"    failed: {e}")
            last = e
    raise SystemExit(f"all Overpass endpoints failed: {last}")


def main():
    res = overpass(QUERY)
    els = res["elements"]
    feats = []

    # ---- lines: merge every route relation of a given ref into one feature. A line has
    # several relations (each direction, short-turn services) that each cover part of the
    # track; taking just one leaves gaps (e.g. a "Taipower Building => Songshan" short-turn).
    # Collect all member ways, deduped by way id, so each line's geometry is complete. ----
    from collections import defaultdict
    lineSegs = defaultdict(list)
    lineMeta = {}
    seenWay = set()
    for el in els:
        if el.get("type") != "relation":
            continue
        t = el.get("tags", {})
        if t.get("route") != "subway":
            continue
        ref = t.get("ref") or t.get("name") or str(el["id"])
        color = t.get("colour") or t.get("color")
        meta = lineMeta.setdefault(ref, {"name": None, "ref": t.get("ref", ""), "color": None})
        if not meta["name"] and (t.get("name:en") or t.get("name")):
            # prefer a full route name over a short-turn ("A => B") where possible
            nm = t.get("name:en") or t.get("name")
            meta["name"] = nm
        if color and not meta["color"]:
            meta["color"] = color
        for m in el.get("members", []):
            if m.get("type") != "way" or not m.get("geometry"):
                continue
            wid = m.get("ref")
            if (ref, wid) in seenWay:
                continue
            seenWay.add((ref, wid))
            lineSegs[ref].append([[p["lon"], p["lat"]] for p in m["geometry"]])
    for ref, segs in lineSegs.items():
        meta = lineMeta[ref]
        name = LINE_NAMES.get(meta["ref"])
        if not name:
            name = meta["name"] or ref
            if "新北投" in name or "Xinbeitou" in name:
                name = "Xinbeitou Branch"
        color = LINE_COLORS.get(meta["ref"]) or meta["color"] or "#888888"
        feats.append({
            "type": "Feature",
            "properties": {"kind": "line", "name": name, "ref": meta["ref"], "color": color},
            "geometry": {"type": "MultiLineString", "coordinates": segs},
        })

    # ---- stations: unique by English name (interchanges map to several nodes) ----
    seenName = set()
    for el in els:
        if el.get("type") != "node":
            continue
        t = el.get("tags", {})
        nameEn = t.get("name:en") or t.get("name")
        if not nameEn or nameEn in seenName:
            continue
        seenName.add(nameEn)
        feats.append({
            "type": "Feature",
            "properties": {"kind": "station", "name": nameEn, "nameZh": t.get("name", "")},
            "geometry": {"type": "Point", "coordinates": [el["lon"], el["lat"]]},
        })

    fc = {"type": "FeatureCollection", "features": feats}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(fc, fh, ensure_ascii=False, separators=(",", ":"))
    nLine = sum(f["properties"]["kind"] == "line" for f in feats)
    nSt = sum(f["properties"]["kind"] == "station" for f in feats)
    print(f"lines: {nLine}  stations: {nSt}  ->  {OUT}  ({os.path.getsize(OUT):,} bytes)")
    for f in feats:
        if f["properties"]["kind"] == "line":
            print(f"  {f['properties']['ref']:>4}  {f['properties']['color']:>8}  {f['properties']['name']}")


if __name__ == "__main__":
    main()
