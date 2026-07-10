"""Download historical LVR data (quarterly "season" releases) from the MOI.

Taiwan's actual-price registration began 2012 Q3 (ROC 101 S3), and the Ministry
of the Interior publishes every quarter since as a bulk ZIP in the same CSV
format this project already parses. This fetches a range of those seasons into
per-season folders that ``buildDatabase.py --seasons-dir`` can then ingest.

    python fetchHistory.py                 # everything from 2012 Q3 to now
    python fetchHistory.py --from 2022     # from 2022 Q1
    python fetchHistory.py --from 2024 --to 2025

Notes:
* The MOI server presents a self-signed certificate chain, so TLS verification is
  relaxed for this host only (public open data — low risk).
* Already-downloaded seasons are skipped, so re-runs are cheap and resumable.
* Seasons that don't exist yet (future/undisclosed) are skipped automatically.
"""

from __future__ import annotations

import argparse
import io
import os
import ssl
import time
import urllib.request
import zipfile

ROC_OFFSET = 1911
FIRST = (101, 3)   # 2012 Q3 — registration began; nothing earlier exists
URL = "https://plvr.land.moi.gov.tw/DownloadSeason?season={s}&type=zip&fileName=lvr_landcsv.zip"
HERE = os.path.dirname(os.path.abspath(__file__))
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE   # MOI cert chain is self-signed


def _seasonsBetween(start, end):
    """Yield 'NNNSQ' season codes from start(inclusive) to end(inclusive)."""
    roc, q = start
    while (roc, q) <= end:
        yield f"{roc}S{q}"
        q += 1
        if q > 4:
            q, roc = 1, roc + 1


def _defaultEnd():
    import datetime
    today = datetime.date.today()
    return (today.year - ROC_OFFSET, (today.month - 1) // 3 + 1)


def _toSeason(yearArg, default):
    """Accept '2024', '2024Q3', '113S3', or a (roc,q) — return (roc, q)."""
    if yearArg is None:
        return default
    s = str(yearArg).upper().replace("Q", "S")
    if "S" in s:
        roc, q = s.split("S")
        roc = int(roc)
        if roc > 1000:            # a Gregorian year like 2024S3
            roc -= ROC_OFFSET
        return (roc, int(q))
    year = int(s)
    roc = year - ROC_OFFSET if year > 1000 else year
    return (roc, 1)


def fetchSeason(season, destDir, delay):
    folder = os.path.join(destDir, season)
    if os.path.exists(os.path.join(folder, "manifest.csv")):
        return "cached"
    try:
        req = urllib.request.Request(URL.format(s=season), headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=120, context=_CTX).read()
    except Exception as e:
        return f"error ({e})"
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        return "missing"          # server returned an error page, not a zip
    if "manifest.csv" not in zf.namelist():
        return "empty"
    os.makedirs(folder, exist_ok=True)
    zf.extractall(folder)
    time.sleep(delay)
    return f"downloaded ({len(raw) / 1e6:.1f} MB)"


def main() -> int:
    p = argparse.ArgumentParser(description="Download historical LVR season releases from the MOI.")
    p.add_argument("--from", dest="frm", default=None, help="start year/season (default 2012 Q3)")
    p.add_argument("--to", dest="to", default=None, help="end year/season (default: current)")
    p.add_argument("--dest", default=os.path.join(HERE, "sourceData"), help="output folder of per-season dirs")
    p.add_argument("--delay", type=float, default=0.5, help="polite delay between downloads (s)")
    args = p.parse_args()

    start = _toSeason(args.frm, FIRST)
    if start < FIRST:
        start = FIRST
    end = _toSeason(args.to, _defaultEnd())
    os.makedirs(args.dest, exist_ok=True)

    seasons = list(_seasonsBetween(start, end))
    print(f"Fetching {len(seasons)} seasons {seasons[0]}..{seasons[-1]} -> {args.dest}")
    counts = {"downloaded": 0, "cached": 0, "missing": 0, "empty": 0, "error": 0}
    for s in seasons:
        status = fetchSeason(s, args.dest, args.delay)
        key = status.split(" ")[0]
        counts[key] = counts.get(key, 0) + 1
        print(f"  {s}: {status}")
    print(f"\nDone. {counts}")
    print(f"Now build with:  python buildDatabase.py --seasons-dir \"{args.dest}\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
