# Deployment Guide

The `webApp/` folder is a **fully static site** — HTML + CSS + JS that fetch the exported
`dataFiles/*` directly in the browser. There is **no backend**, so it hosts for **free, 24/7** on any
static host. External libraries (Leaflet, Chart.js, sql.js) load from public CDNs, so nothing else needs
hosting.

- **Total deploy size ≈ 56 MB** — a 19 MB browse-sample SQLite + ~35 MB of per-city drill-down records.
  The records load **lazily, one city at a time**, so the *initial* page transfer stays a few MB. Well
  within every free tier (GitHub Pages ~1 GB, Netlify/Cloudflare far more).
- Publish the **contents of `webApp/`** at the site root so the relative `dataFiles/…`, `styles/…`,
  `scripts/…` paths resolve. All internal paths are relative, so it also works fine under a subpath like
  `you.github.io/taiwanHousing/`.

## (Re)generate the data before deploying

```bash
python fetchHistory.py                                                      # optional: pull newest LVR seasons
python modeling/buildDataset.py                                             # sales.parquet (+ --txn-suffix b => presale)
python buildDatabase.py --seasons-dir sourceData --sales-only --no-subtables   # web dataFiles + local DB
python publishSampleDb.py            # 19 MB browse-sample sqlite
python geocodeDoorplate.py           # real map coords for configured counties (門牌 geocoding)
python exportSeriesCsv.py            # marketSeriesMonthly.csv (tidy series)
python modeling/exportPredictor.py   # predictor.json (client-side model + provenance stamp)
```

Bump the `?v=` query strings in `index.html` / `predictor.html` (and `DATA_V` in `appMain.js`) when data
changes so visitors' browsers refetch.

## Option A — Netlify / Cloudflare Pages (drag-and-drop, fastest to a URL)

1. Go to Netlify Drop (app.netlify.com/drop) or Cloudflare Pages → "Upload assets".
2. Drag the **`webApp/`** folder in.
3. Done — a permanent `https://…` URL on a global CDN, 24/7, $0. (Create a free account to keep/rename it.)

## Option B — GitHub Pages via Actions (versioned, auto-deploy — recommended)

A workflow at `.github/workflows/deploy.yml` publishes `webApp/` automatically. One-time setup, from
`taiwanHousing/`:

```bash
git add . && git commit -m "Taiwan Housing Explorer"   # (already done if repo exists)
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

`.gitignore` already excludes the 2.8 GB DB, `sourceData/`, and the `*.parquet` warehouse, so only the
code + the ~56 MB `webApp/dataFiles/` are pushed. Then in **Settings → Pages** set **Source = "GitHub
Actions"** (not a branch). The workflow runs and the site appears at `https://<you>.github.io/<repo>/`.
Public repos get Pages free; for a *private* repo Pages needs GitHub Pro (free via the Student pack).

## Updating later (the whole point)

Every push to `main` that touches `webApp/` **auto-redeploys** — no manual publish step:

```bash
git add -A && git commit -m "Refresh data" && git push
```

Or edit a file straight in the GitHub web UI (press `.` in the repo to open the browser editor) and
commit — the Action redeploys in ~1 minute. Re-run the regenerate block above first when the underlying
data changes; the map, series, and predictor all deepen automatically as more LVR seasons are added.

## Notes

- The map uses free OpenStreetMap tiles. For very high traffic, swap the tile URL in
  `webApp/scripts/appMain.js` for a keyed provider (MapTiler, Stadia, etc.).
- `webApp/dataFiles/taiwanHousing.sqlite` (~19 MB) powers the in-browser **Browse database** page and the
  sidebar download link. To not ship it, delete that file and remove the link/`database.html` before deploying.
- The 2.8 GB local `database/taiwanHousing.sqlite` and `modeling/data/*.parquet` are **never** deployed —
  they're regenerable build artifacts (see `modelCard.md`).
