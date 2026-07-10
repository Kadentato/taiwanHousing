/* Taiwan Housing Explorer — fully client-side dashboard.
 * Loads the exported summary + GeoJSON geometry + per-city records, then
 * aggregates live so transaction-type, hierarchy and tag filters all drive the
 * map and the time chart from a single source of truth (the records). */

const DATA = "dataFiles/";
const DATA_V = "?v=12";  // bump on rebuild so browsers refetch updated data files
const PALETTE = ["#ffffcc", "#c7e9b4", "#7fcdbb", "#41b6c4", "#2c7fb8", "#253494"];
const NO_DATA = "#e5e7eb";

const state = {
  type: "sale",
  level: "city",
  metric: "unit",
  scopeRegion: "",
  scopeCity: "",
  tags: new Set(),
  scopeDistrict: "",    // set when drilled to individual houses
  view: "map",          // "map" | "table"
  sortKey: "unit",      // must match a COLUMNS key
  sortDir: "desc",
  page: 0,
  // statistical controls
  minN: 1,              // hide aggregates below this sample size
  excludeFlags: new Set(),   // deal-quality flags to drop (relatedPartyDeal, ...)
  winsorize: false,     // trim the top/bottom 1% of unit price
  yearFrom: null, yearTo: null,   // transaction-year window (null = all)
  fixedScale: false,    // fixed vs adaptive colour bins
  colorMode: "metric",  // "metric" | "lisa"
};
const PAGE_SIZE = 50;
const TAIWAN_BOUNDS = L.latLngBounds([21.5, 118.0], [25.6, 122.3]);
const viewStack = [];   // drill history for Esc (each entry restores level+scope+map view)
let DEFAULT_YEAR_FROM = null;   // map/stats default to a recent window, not the pooled decade

// Hover descriptions for the transaction-type buttons: what each represents and
// which fields it stores. (Record counts are filled in live from summary.json.)
const TYPE_INFO = {
  sale: {
    title: "Sale — completed real-estate sales",
    body: "Finished purchases of existing property that actually changed hands. Stores sale price, unit "
      + "price, size, layout, building type, age, and parking.",
  },
  presale: {
    title: "Pre-sale — off-plan sales",
    body: "Units bought from the developer before the building is finished. Stores contract price, planned "
      + "size and layout, project name, and whether the deal was later cancelled.",
  },
  rental: {
    title: "Rental — lease agreements",
    body: "Leases rather than purchases, so the price shown is monthly rent. Stores rent, size, layout, "
      + "lease period, and whether it's furnished.",
  },
};

const store = {
  summary: null,
  records: [],
  geom: { region: new Map(), city: new Map(), district: new Map() },
  cityByCode: new Map(),
  regionById: new Map(),
  districtById: new Map(),
};

let map, dataLayer, legend, chart;

// ----------------------------------------------------------------- helpers ---
const median = (arr) => quantile(arr, 0.5);

// Percentile of an unsorted numeric array (linear interpolation).
function quantile(arr, p) {
  if (!arr.length) return null;
  const s = [...arr].sort((a, b) => a - b);
  const idx = (s.length - 1) * p;
  const lo = Math.floor(idx), hi = Math.ceil(idx);
  return lo === hi ? s[lo] : s[lo] + (s[hi] - s[lo]) * (idx - lo);
}

// 95% bootstrap CI for the median (used for the current selection only — cheap).
function bootstrapMedianCI(values, iters = 800) {
  const v = values.filter((x) => x != null);
  if (v.length < 8) return null;
  const meds = [];
  for (let b = 0; b < iters; b++) {
    const s = [];
    for (let i = 0; i < v.length; i++) s.push(v[(Math.random() * v.length) | 0]);
    meds.push(quantile(s, 0.5));
  }
  return [quantile(meds, 0.025), quantile(meds, 0.975)];
}

// Each metric knows how to read a per-record value, its raw distribution field,
// and formatting. pick() = median over records. All prices are nominal NT$.
const METRIC = {
  unit:  { label: "Median unit price", short: "unit price", unit: "NT$/m²",
           fmt: (v) => "NT$" + Math.round(v).toLocaleString() + "/m²",
           val: (r) => (r.unitPricePerM2 == null ? null : r.unitPricePerM2) },
  total: { label: "Median total price", short: "total price", unit: "NT$",
           fmt: (v) => v >= 1e6 ? "NT$" + (v / 1e6).toFixed(2) + "M" : "NT$" + Math.round(v).toLocaleString(),
           val: (r) => (r.totalPrice == null ? null : r.totalPrice) },
  count: { label: "Transactions", short: "count", unit: "",
           fmt: (v) => v.toLocaleString(), val: () => 1 },
  ping:  { label: "Median size", short: "size", unit: "ping",
           fmt: (v) => v.toFixed(1) + " ping",
           val: (r) => (r.livingAreaPing == null ? null : r.livingAreaPing) },
};
for (const m of Object.values(METRIC)) {
  m.values = (rs) => rs.map(m.val).filter((v) => v != null);
  m.pick = (rs) => (m === METRIC.count ? rs.length : median(m.values(rs)));
}

// Tag dimension + predicate (OR within a dimension, AND across dimensions).
function tagInfo(slug) {
  if (slug === "hasParking") return ["parkingPresence", (r) => r.hasParking === 1];
  if (slug === "noParking") return ["parkingPresence", (r) => r.hasParking === 0];
  if (slug.startsWith("parkingType:")) {
    const t = slug.split(":")[1];
    return ["parkingType", (r) => r.parkingType === t];
  }
  if (slug === "hasManagementOrg") return ["managementOrg", (r) => r.hasManagementOrg === 1];
  if (slug === "noManagementOrg") return ["managementOrg", (r) => r.hasManagementOrg === 0];
  if (slug === "hasElevator") return ["elevator", (r) => r.hasElevator === 1];
  if (slug === "noElevator") return ["elevator", (r) => r.hasElevator === 0];
  return ["other", () => true];
}

// --------------------------------------------------------------- filtering ---
function filteredRecords(opts = {}) {
  // Group active tags by dimension.
  const dims = new Map();
  for (const slug of state.tags) {
    const [dim, pred] = tagInfo(slug);
    if (!dims.has(dim)) dims.set(dim, []);
    dims.get(dim).push(pred);
  }
  let out = store.records.filter((r) => {
    if (r.transactionType !== state.type) return false;
    if (state.level === "houses" && state.scopeDistrict && r.districtId !== Number(state.scopeDistrict)) return false;
    if (state.scopeCity && r.cityCode !== state.scopeCity) return false;
    if (state.scopeRegion && r.regionId !== Number(state.scopeRegion)) return false;
    if (!opts.ignoreYear) {
      if (state.yearFrom && r.saleYear < state.yearFrom) return false;
      if (state.yearTo && r.saleYear > state.yearTo) return false;
    }
    for (const flag of state.excludeFlags) if (r[flag] === 1) return false;
    for (const preds of dims.values()) {
      if (!preds.some((p) => p(r))) return false;
    }
    return true;
  });
  // Winsorize (trim) the extreme 1% of unit price for market-focused views.
  if (state.winsorize && out.length > 20) {
    const up = out.map((r) => r.unitPricePerM2).filter((v) => v != null);
    const lo = quantile(up, 0.01), hi = quantile(up, 0.99);
    out = out.filter((r) => r.unitPricePerM2 == null || (r.unitPricePerM2 >= lo && r.unitPricePerM2 <= hi));
  }
  return out;
}

function groupBy(records, keyFn) {
  const m = new Map();
  for (const r of records) {
    const k = keyFn(r);
    if (k == null) continue;
    if (!m.has(k)) m.set(k, []);
    m.get(k).push(r);
  }
  return m;
}

function quantileBins(values) {
  const s = values.filter((v) => v != null).sort((a, b) => a - b);
  if (s.length < 2) return s.length ? [s[0]] : [];
  const q = (p) => s[Math.min(s.length - 1, Math.floor(p * s.length))];
  return [q(0.17), q(0.34), q(0.5), q(0.67), q(0.84)];
}
function colorFor(value, bins) {
  if (value == null) return NO_DATA;
  let i = 0;
  while (i < bins.length && value > bins[i]) i++;
  return PALETTE[Math.min(i, PALETTE.length - 1)];
}

// ------------------------------------------------------------------- map ----
// Bubble radius in pixels, area roughly proportional to count (min 5, max ~23).
const bubbleRadius = (count, maxCount) => 5 + 18 * Math.sqrt(count / Math.max(1, maxCount));

function levelKeyFn() {
  if (state.level === "region") return (r) => r.regionId;
  if (state.level === "city") return (r) => r.cityCode;
  return (r) => r.districtId;
}

function featuresForLevel() {
  const g = store.geom[state.level];
  let feats = [...g.values()];
  if (state.level === "city") {
    if (state.scopeRegion) feats = feats.filter((f) => f.properties.regionId === Number(state.scopeRegion));
  } else if (state.level === "district") {
    if (state.scopeCity) feats = feats.filter((f) => f.properties.cityCode === state.scopeCity);
    else if (state.scopeRegion) feats = feats.filter((f) => f.properties.regionId === Number(state.scopeRegion));
  } else if (state.level === "region") {
    if (state.scopeRegion) feats = feats.filter((f) => f.properties.regionId === Number(state.scopeRegion));
  }
  return feats;
}

function featureKey(feature) {
  if (state.level === "region") return feature.properties.regionId;
  if (state.level === "city") return feature.properties.cityCode;
  return feature.properties.districtId;
}

function featureName(feature) {
  const p = feature.properties;
  if (state.level === "region") return store.regionById.get(p.regionId)?.nameEn || "Region";
  if (state.level === "city") return p.cityEn;
  return (p.cityEn || "") + " · " + (p.districtEn || p.districtZh);
}

// Short English label for the map (romanised district name, English city/region).
function featureLabel(feature) {
  const p = feature.properties;
  if (state.level === "region") return store.regionById.get(p.regionId)?.nameEn || "";
  if (state.level === "city") return p.cityEn || "";
  return p.districtEn || p.districtZh || "";
}

const districtLabelOf = (id) => {
  const d = store.districtById.get(Number(id));
  return d ? (d.nameEn || d.nameZh) : "";
};

const LISA_COLORS = { HH: "#d7191c", LL: "#2c7bb6", HL: "#fdae61", LH: "#abd9e9", ns: "#e5e7eb" };
const _globalBins = {};

// Fixed colour bins: quantiles of the per-group medians over ALL of this type
// (ignores scope/tags/time) so a district's colour is comparable across views.
function globalBins(metric) {
  const key = [metric.short, state.type, state.level].join("|");
  if (!_globalBins[key]) {
    const g = groupBy(store.records.filter((r) => r.transactionType === state.type), levelKeyFn());
    _globalBins[key] = quantileBins([...g.values()].map((rs) => metric.pick(rs)).filter((v) => v != null));
  }
  return _globalBins[key];
}

function renderMap() {
  if (dataLayer) { dataLayer.remove(); dataLayer = null; }
  if (state.level === "houses") { renderHouses(); return; }

  const grouped = groupBy(filteredRecords(), levelKeyFn());
  const metric = METRIC[state.metric];
  const lisaMode = state.colorMode === "lisa" && state.level === "district";
  const enough = (rs) => rs.length >= state.minN;

  const bins = lisaMode ? []
    : (state.fixedScale ? globalBins(metric)
       : quantileBins([...grouped.values()].filter(enough).map((rs) => metric.pick(rs))));

  const fillFor = (feature) => {
    const rs = grouped.get(featureKey(feature)) || [];
    if (!enough(rs)) return NO_DATA;                       // small-n greyed out
    if (lisaMode) return LISA_COLORS[feature.properties.lisa] || NO_DATA;
    return colorFor(metric.pick(rs), bins);
  };

  const feats = featuresForLevel();
  const fc = { type: "FeatureCollection", features: feats };

  const onEach = (feature, layer) => {
    const rs = grouped.get(featureKey(feature)) || [];
    layer.bindPopup(popupHtml(featureName(feature), rs, feature));
    if (enough(rs)) layer.on("click", () => drillInto(feature));
    const label = featureLabel(feature);
    if (label) {
      if (state.level === "district") layer.bindTooltip(label, { direction: "top", className: "mapLabel" });
      else layer.bindTooltip(label, { permanent: true, direction: "center", className: "mapLabel" });
    }
  };

  if (state.level === "district") {
    const maxCount = Math.max(1, ...[...grouped.values()].map((rs) => rs.length));
    dataLayer = L.geoJSON(fc, {
      pointToLayer: (feature, latlng) => L.circleMarker(latlng, {
        radius: bubbleRadius((grouped.get(featureKey(feature)) || []).length, maxCount),
        fillColor: fillFor(feature), color: "#334155", weight: 0.8, fillOpacity: 0.85,
      }),
      onEachFeature: onEach,
    }).addTo(map);
  } else {
    dataLayer = L.geoJSON(fc, {
      style: (f) => ({ fillColor: fillFor(f), color: "#fff", weight: 1, fillOpacity: 0.78 }),
      onEachFeature: onEach,
    }).addTo(map);
  }

  if (lisaMode) updateLisaLegend();
  else updateLegend(bins, metric, null, state.level === "district");
}

function updateLisaLegend() {
  if (legend) legend.remove();
  legend = L.control({ position: "bottomright" });
  legend.onAdd = () => {
    const div = L.DomUtil.create("div", "legend");
    const rows = [["HH", "High–High (hot spot)"], ["LL", "Low–Low (cold spot)"],
                  ["HL", "High–Low (outlier)"], ["LH", "Low–High (outlier)"], ["ns", "Not significant"]];
    div.innerHTML = `<div class="legendTitle">Price clusters (LISA, p<0.05)</div>`
      + rows.map(([k, l]) => `<div><i style="background:${LISA_COLORS[k]}"></i>${l}</div>`).join("");
    return div;
  };
  legend.addTo(map);
}

// --- individual house points (level "houses") --------------------------------
// The LVR open data has no per-house coordinates, so each transaction is drawn as
// a separate point deterministically jittered within its district and coloured by
// the chosen metric — so you can see the spread of individual homes, and clicking
// one shows its details. Positions are illustrative, not exact addresses.
function renderHouses() {
  const feat = store.geom.district.get(Number(state.scopeDistrict));
  const rs = filteredRecords();
  if (!feat) { updateLegend([], METRIC[state.metric], "No location for this district"); return; }
  const [lon, lat] = feat.geometry.coordinates;
  const metric = METRIC[state.metric === "count" ? "unit" : state.metric];
  const bins = quantileBins(metric.values(rs));
  const R = 0.014;                                   // ~1.4 km jitter radius, in degrees
  const cosLat = Math.cos(lat * Math.PI / 180) || 1;

  const markers = rs.map((r, i) => {
    // two low-discrepancy sequences -> stable, evenly-spread jitter (no reshuffle on re-render)
    const angle = 2 * Math.PI * ((i * 0.6180339887) % 1);
    const rad = R * Math.sqrt((i * 0.7548776662 + 0.13) % 1);
    const v = metric.val(r);
    return L.circleMarker([lat + rad * Math.sin(angle), lon + rad * Math.cos(angle) / cosLat], {
      radius: 4.5, fillColor: v == null ? NO_DATA : colorFor(v, bins),
      color: "#1e293b", weight: 0.5, fillOpacity: 0.82,
    }).bindPopup(housePopup(r));
  });
  dataLayer = L.featureGroup(markers).addTo(map);
  updateLegend(bins, metric,
    `${rs.length.toLocaleString()} individual ${state.type} homes · each dot is one sale, `
    + `coloured by ${metric.short} · positions jittered within the district (no exact addresses in the open data)`);
}

// Popup for a single transaction (individual house drill-in).
function housePopup(r) {
  const row = (l, v) => `<div class="popupStat"><span>${l}</span><b>${v}</b></div>`;
  return `<b>${pretty(r.buildingType) || "Property"}</b>`
    + row("Unit price", r.unitPricePerM2 != null ? METRIC.unit.fmt(r.unitPricePerM2) : "—")
    + row("Total price", r.totalPrice != null ? METRIC.total.fmt(r.totalPrice) : "—")
    + row("Living size", r.livingAreaPing != null ? METRIC.ping.fmt(r.livingAreaPing) : "—")
    + row("Layout", `${r.bedrooms ?? "?"} bd / ${r.bathrooms ?? "?"} ba`)
    + row("Age", r.buildingAgeYears != null ? r.buildingAgeYears + " yrs" : "—")
    + row("Parking", parkingStr(r)) + row("Elevator", yesNo(r.hasElevator))
    + row("Date", monthStr(r));
}

const LISA_LABEL = { HH: "High–High", LL: "Low–Low", HL: "High–Low", LH: "Low–High" };

function popupHtml(name, rs, feature) {
  const row = (label, val) => `<div class="popupStat"><span>${label}</span><b>${val}</b></div>`;
  if (!rs.length) return `<b>${name}</b><div class="popupStat"><span>No ${state.type} records</span></div>`;
  const uVals = METRIC.unit.values(rs);
  const u = median(uVals), q1 = quantile(uVals, 0.25), q3 = quantile(uVals, 0.75);
  const t = METRIC.total.pick(rs), p = METRIC.ping.pick(rs);
  let html = `<b>${name}</b>` + row("Transactions (n)", rs.length.toLocaleString());
  if (rs.length < state.minN) html += `<div class="popupStat"><span style="color:#dc2626">below min n (${state.minN})</span></div>`;
  html += (u != null ? row("Median unit price", METRIC.unit.fmt(u)) : "")
    + (q1 != null ? row("IQR", METRIC.unit.fmt(q1) + " – " + METRIC.unit.fmt(q3)) : "")
    + (t != null ? row("Median total", METRIC.total.fmt(t)) : "")
    + (p != null ? row("Median size", METRIC.ping.fmt(p)) : "");
  const lisa = feature && feature.properties && feature.properties.lisa;
  if (lisa && lisa !== "ns") html += row("Price cluster", LISA_LABEL[lisa] || lisa);
  return html;
}

function boundsOfFeature(feature) { return L.geoJSON(feature).getBounds(); }
function zoomToBounds(b) { if (b && b.isValid()) map.fitBounds(b, { padding: [30, 30], maxZoom: 15 }); }

function fitToScope() {
  if (state.scopeCity) { const f = store.geom.city.get(state.scopeCity); if (f) return zoomToBounds(boundsOfFeature(f)); }
  if (state.scopeRegion) { const f = store.geom.region.get(Number(state.scopeRegion)); if (f) return zoomToBounds(boundsOfFeature(f)); }
  map.fitBounds(TAIWAN_BOUNDS);
}

function pushView() {
  viewStack.push({
    level: state.level, scopeRegion: state.scopeRegion, scopeCity: state.scopeCity,
    scopeDistrict: state.scopeDistrict, center: map.getCenter(), zoom: map.getZoom(),
  });
}

// Esc: return to the previous view (pops one drill step and restores its zoom).
function popView() {
  if (!viewStack.length) { fitToScope(); return; }
  const prev = viewStack.pop();
  state.level = prev.level; state.scopeRegion = prev.scopeRegion;
  state.scopeCity = prev.scopeCity; state.scopeDistrict = prev.scopeDistrict;
  syncControls();
  renderAll();
  map.setView(prev.center, prev.zoom, { animate: true });
}

function drillInto(feature) {
  pushView();
  if (state.level === "region") {
    state.scopeRegion = String(feature.properties.regionId);
    state.level = "city";
    syncControls(); renderAll();
    zoomToBounds(boundsOfFeature(feature));
    return;
  }
  if (state.level === "city") {
    state.scopeRegion = String(feature.properties.regionId);
    state.scopeCity = feature.properties.cityCode;
    state.level = "district";
    syncControls(); renderAll();
    zoomToBounds(boundsOfFeature(feature));
    return;
  }
  if (state.level === "district") {
    state.scopeDistrict = String(feature.properties.districtId);
    state.level = "houses";
    syncControls(); renderAll();
    const c = store.geom.district.get(Number(state.scopeDistrict));
    if (c) map.setView([c.geometry.coordinates[1], c.geometry.coordinates[0]], 13);
    return;
  }
  viewStack.pop(); // houses are leaves — nothing to drill; undo the push
}

function updateLegend(bins, metric, note, showSize) {
  if (legend) legend.remove();
  legend = L.control({ position: "bottomright" });
  legend.onAdd = () => {
    const div = L.DomUtil.create("div", "legend");
    const scaleTag = state.fixedScale ? " (fixed)" : "";
    let html = `<div class="legendTitle">${metric.label}${scaleTag}</div>`;
    const n = bins.length; // thresholds -> n+1 colour buckets (matches colorFor)
    for (let i = 0; i <= n; i++) {
      let label;
      if (n === 0) label = "All values";
      else if (i === 0) label = "≤ " + metric.fmt(bins[0]);
      else if (i === n) label = "> " + metric.fmt(bins[n - 1]);
      else label = metric.fmt(bins[i - 1]) + " – " + metric.fmt(bins[i]);
      html += `<div><i style="background:${PALETTE[i]}"></i>${label}</div>`;
    }
    html += `<div><i style="background:${NO_DATA}"></i>No data / n < ${state.minN}</div>`;
    if (showSize) html += `<div class="legendSize"><svg width="76" height="24">`
      + `<circle cx="9" cy="17" r="4" fill="#9ca3af"/><circle cx="30" cy="14" r="7" fill="#9ca3af"/>`
      + `<circle cx="58" cy="12" r="11" fill="#9ca3af"/></svg><span>bubble size ∝ n</span></div>`;
    if (note) html += `<div class="legendNote">${note}</div>`;
    div.innerHTML = html;
    return div;
  };
  legend.addTo(map);
}

// ------------------------------------------------------------------ chart ---
const THIN_MONTH_N = 10;   // below this, a month's median is too noisy to trust

function renderChart() {
  if (chart) { chart.destroy(); chart = null; }
  if (state.level === "houses") return renderDistributionChart();
  renderTimeChart();
}

function renderTimeChart() {
  document.getElementById("chartTitle").textContent = "Transactions disclosed this release, by transaction date";
  document.getElementById("chartHint").textContent =
    `full history (ignores the year filter) · pale bars: n < ${THIN_MONTH_N} (noisy)`;
  // The chart shows the whole history even though the map/stats default to a recent window.
  const byMonth = groupBy(
    filteredRecords({ ignoreYear: true }).filter((r) => r.saleYear && r.saleMonth),
    (r) => `${r.saleYear}-${String(r.saleMonth).padStart(2, "0")}`
  );
  const months = [...byMonth.keys()].sort();
  const counts = months.map((m) => byMonth.get(m).length);
  // Median only where n is adequate; thin months are shaded pale so the noisy tail doesn't read as a trend.
  const medUnit = months.map((m) => byMonth.get(m).length >= THIN_MONTH_N ? METRIC.unit.pick(byMonth.get(m)) : null);
  const barColors = counts.map((n) => n >= THIN_MONTH_N ? "#93c5fd" : "#e2e8f0");

  chart = new Chart(document.getElementById("timeChart"), {
    data: {
      labels: months,
      datasets: [
        { type: "bar", label: "Transactions (n)", data: counts, yAxisID: "y",
          backgroundColor: barColors, borderColor: barColors, order: 2 },
        { type: "line", label: "Median unit price", data: medUnit, yAxisID: "y1",
          borderColor: "#1d4ed8", backgroundColor: "#1d4ed8", tension: 0.25,
          pointRadius: 2, spanGaps: false, order: 1 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { ticks: { maxTicksLimit: 14, font: { size: 10 } }, grid: { display: false } },
        y: { position: "left", title: { display: true, text: "n" }, beginAtZero: true },
        y1: { position: "right", title: { display: true, text: METRIC.unit.unit }, grid: { drawOnChartArea: false } },
      },
      plugins: { legend: { labels: { boxWidth: 12, font: { size: 11 } } } },
    },
  });
}

// District drill-down: a strip plot of the individual transactions positioned by
// the metric value (a real axis) — replaces the earlier invented map coordinates.
function renderDistributionChart() {
  const metric = METRIC[state.metric === "count" ? "unit" : state.metric];
  const rs = filteredRecords();
  const pts = rs.map((r) => ({ v: metric.val(r), r })).filter((d) => d.v != null);
  const med = quantile(pts.map((d) => d.v), 0.5);
  const q1 = quantile(pts.map((d) => d.v), 0.25), q3 = quantile(pts.map((d) => d.v), 0.75);

  document.getElementById("chartTitle").textContent = `${scopeLabel()} — ${metric.short} distribution`;
  document.getElementById("chartHint").textContent =
    `${pts.length} transactions · median ${med != null ? metric.fmt(med) : "—"}`
    + (q1 != null ? ` · IQR ${metric.fmt(q1)}–${metric.fmt(q3)}` : "") + " · each dot is one deal";

  const vline = (x) => x == null ? [] : [{ x, y: 0 }, { x, y: 1 }];
  chart = new Chart(document.getElementById("timeChart"), {
    data: {
      datasets: [
        { type: "scatter", label: "Transactions",
          // deterministic golden-ratio y so points don't reshuffle on re-render
          data: pts.map((d, i) => ({ x: d.v, y: ((i * 0.618033988749895) % 1) * 0.8 + 0.1, rec: d.r })),
          pointRadius: 3, backgroundColor: "rgba(37,127,184,0.55)", borderWidth: 0 },
        { type: "line", label: "Median", data: vline(med), borderColor: "#dc2626", borderWidth: 1.5, pointRadius: 0 },
        { type: "line", label: "Q1", data: vline(q1), borderColor: "#94a3b8", borderWidth: 1, borderDash: [4, 3], pointRadius: 0 },
        { type: "line", label: "Q3", data: vline(q3), borderColor: "#94a3b8", borderWidth: 1, borderDash: [4, 3], pointRadius: 0 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: {
        x: { title: { display: true, text: metric.short + (metric.unit ? " (" + metric.unit + ")" : "") }, beginAtZero: false },
        y: { min: 0, max: 1, display: false },
      },
      plugins: {
        legend: { labels: { boxWidth: 12, font: { size: 11 }, filter: (i) => i.text !== "Q1" && i.text !== "Q3" } },
        tooltip: { callbacks: { label: (ctx) => {
          const r = ctx.raw.rec; if (!r) return "";
          return `${pretty(r.buildingType) || "Property"} · ${metric.fmt(ctx.raw.x)} · ${r.bedrooms ?? "?"}bd/${r.bathrooms ?? "?"}ba · ${monthStr(r)}`;
        } } },
      },
    },
  });
}

// ------------------------------------------------------------- stats card ---
function scopeLabel() {
  if (state.level === "houses" && state.scopeDistrict) {
    const c = store.cityByCode.get(state.scopeCity);
    return (c?.nameEn ? c.nameEn + " · " : "") + (districtLabelOf(state.scopeDistrict) || "District");
  }
  if (state.scopeCity) return store.cityByCode.get(state.scopeCity)?.nameEn || "City";
  if (state.scopeRegion) return store.regionById.get(Number(state.scopeRegion))?.nameEn || "Region";
  return "All Taiwan";
}

function renderStats() {
  const rs = filteredRecords();
  const row = (l, v) => `<div class="statRow"><span>${l}</span><b>${v}</b></div>`;
  const uVals = METRIC.unit.values(rs);
  const u = median(uVals), q1 = quantile(uVals, 0.25), q3 = quantile(uVals, 0.75);
  const ci = bootstrapMedianCI(uVals);
  const t = METRIC.total.pick(rs), p = METRIC.ping.pick(rs);
  let html = row("Scope", scopeLabel())
    + row("Type", state.type)
    + (state.yearFrom || state.yearTo ? row("Years", (state.yearFrom || "…") + "–" + (state.yearTo || "latest")) : "")
    + row("Transactions (n)", rs.length.toLocaleString())
    + row("Median unit price", u != null ? METRIC.unit.fmt(u) : "—");
  if (q1 != null && q3 != null) html += row("IQR (Q1–Q3)", METRIC.unit.fmt(q1) + " – " + METRIC.unit.fmt(q3));
  if (ci) html += row("Median 95% CI", METRIC.unit.fmt(ci[0]) + " – " + METRIC.unit.fmt(ci[1]));
  html += row("Median total price", t != null ? METRIC.total.fmt(t) : "—")
    + row("Median living size", p != null ? METRIC.ping.fmt(p) : "—");
  document.getElementById("statsBody").innerHTML = html;
}

// -------------------------------------------------------------- table view ---
const districtNameOf = (r) => districtLabelOf(r.districtId);
const cityNameOf = (r) => store.cityByCode.get(r.cityCode)?.nameEn || "";
const pretty = (s) => (s == null ? "" : String(s).replace(/([a-z])([A-Z])/g, "$1 $2").replace(/^./, (c) => c.toUpperCase()));
const money = (v) => (v == null ? "—" : "NT$" + Math.round(v).toLocaleString());
const yesNo = (v) => (v === 1 ? "Yes" : v === 0 ? "No" : "—");
const monthStr = (r) => (r.saleYear && r.saleMonth ? `${r.saleYear}-${String(r.saleMonth).padStart(2, "0")}` : "—");
const parkingStr = (r) => (r.hasParking === 1 ? (r.parkingType ? pretty(r.parkingType) : "Yes") : "No");

// column: cell (display), sort (comparable), csv (raw for export), num (right-align)
const COLUMNS = [
  { key: "district", label: "District", cell: districtNameOf, sort: districtNameOf },
  { key: "city", label: "City", cell: cityNameOf, sort: cityNameOf },
  { key: "type", label: "Type", cell: (r) => pretty(r.transactionType), sort: (r) => r.transactionType },
  { key: "target", label: "Target", cell: (r) => pretty(r.targetType), sort: (r) => r.targetType },
  { key: "date", label: "Date", cell: monthStr, sort: (r) => (r.saleYear || 0) * 100 + (r.saleMonth || 0), csv: monthStr },
  { key: "total", label: "Total price", num: true, cell: (r) => money(r.totalPrice), sort: (r) => r.totalPrice, csv: (r) => r.totalPrice },
  { key: "unit", label: "Unit NT$/m²", num: true, cell: (r) => money(r.unitPricePerM2), sort: (r) => r.unitPricePerM2, csv: (r) => r.unitPricePerM2 },
  { key: "ping", label: "Living size (ping)", num: true, cell: (r) => (r.livingAreaPing != null ? r.livingAreaPing.toFixed(1) : "—"), sort: (r) => r.livingAreaPing, csv: (r) => r.livingAreaPing },
  { key: "beds", label: "Beds", num: true, cell: (r) => r.bedrooms ?? "—", sort: (r) => r.bedrooms, csv: (r) => r.bedrooms },
  { key: "baths", label: "Baths", num: true, cell: (r) => r.bathrooms ?? "—", sort: (r) => r.bathrooms, csv: (r) => r.bathrooms },
  { key: "building", label: "Building type", cell: (r) => pretty(r.buildingType), sort: (r) => r.buildingType },
  { key: "parking", label: "Parking", cell: parkingStr, sort: parkingStr, csv: (r) => r.parkingType || (r.hasParking === 1 ? "yes" : "no") },
  { key: "elevator", label: "Elevator", cell: (r) => yesNo(r.hasElevator), sort: (r) => r.hasElevator, csv: (r) => r.hasElevator },
  { key: "mgmt", label: "Mgmt org", cell: (r) => yesNo(r.hasManagementOrg), sort: (r) => r.hasManagementOrg, csv: (r) => r.hasManagementOrg },
  { key: "age", label: "Age (yrs)", num: true, cell: (r) => (r.buildingAgeYears != null ? r.buildingAgeYears : "—"), sort: (r) => r.buildingAgeYears, csv: (r) => r.buildingAgeYears },
];

function sortedRecords() {
  const col = COLUMNS.find((c) => c.key === state.sortKey) || COLUMNS[0];
  const dir = state.sortDir === "asc" ? 1 : -1;
  return filteredRecords().slice().sort((a, b) => {
    let x = col.sort(a), y = col.sort(b);
    if (x == null && y == null) return 0;
    if (x == null) return 1;        // nulls always last
    if (y == null) return -1;
    if (typeof x === "string") return x.localeCompare(y) * dir;
    return (x - y) * dir;
  });
}

function renderTable() {
  const rows = sortedRecords();
  const maxPage = Math.max(0, Math.ceil(rows.length / PAGE_SIZE) - 1);
  if (state.page > maxPage) state.page = maxPage;
  const start = state.page * PAGE_SIZE;
  const pageRows = rows.slice(start, start + PAGE_SIZE);

  const thead = document.querySelector("#dataTable thead");
  thead.innerHTML = "<tr>" + COLUMNS.map((c) => {
    const arrow = state.sortKey === c.key ? `<span class="sortArrow">${state.sortDir === "asc" ? "▲" : "▼"}</span>` : "";
    return `<th data-key="${c.key}">${c.label}${arrow}</th>`;
  }).join("") + "</tr>";
  thead.querySelectorAll("th").forEach((th) => {
    th.onclick = () => {
      const key = th.dataset.key;
      if (state.sortKey === key) state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
      else { state.sortKey = key; state.sortDir = "desc"; }
      state.page = 0;
      renderTable();
    };
  });

  const tbody = document.querySelector("#dataTable tbody");
  tbody.innerHTML = pageRows.map((r) =>
    "<tr>" + COLUMNS.map((c) => `<td class="${c.num ? "num" : ""}">${c.cell(r)}</td>`).join("") + "</tr>"
  ).join("");

  const pager = document.getElementById("tablePager");
  const from = rows.length ? start + 1 : 0;
  const to = Math.min(start + PAGE_SIZE, rows.length);
  pager.innerHTML =
    `<button id="pagePrev" ${state.page === 0 ? "disabled" : ""}>‹ Prev</button>`
    + `<span>${from.toLocaleString()}–${to.toLocaleString()} of ${rows.length.toLocaleString()}</span>`
    + `<button id="pageNext" ${state.page >= maxPage ? "disabled" : ""}>Next ›</button>`;
  document.getElementById("pagePrev").onclick = () => { state.page--; renderTable(); };
  document.getElementById("pageNext").onclick = () => { state.page++; renderTable(); };

  document.getElementById("viewBarRight").innerHTML =
    `<span>${rows.length.toLocaleString()} records (${state.type})</span>`
    + `<button class="downloadBtn" id="csvBtn">Download CSV</button>`;
  document.getElementById("csvBtn").onclick = () => downloadCsv(rows);
}

function downloadCsv(rows) {
  const esc = (v) => {
    if (v == null) return "";
    const s = String(v);
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  };
  const header = COLUMNS.map((c) => c.label).join(",");
  const body = rows.map((r) => COLUMNS.map((c) => esc((c.csv || c.cell)(r))).join(",")).join("\n");
  const blob = new Blob(["﻿" + header + "\n" + body], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const scope = (state.scopeCity || (state.scopeRegion ? "region" + state.scopeRegion : "allTaiwan"));
  a.href = url;
  a.download = `taiwanHousing_${state.type}_${scope}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

function setView(view) {
  state.view = view;
  document.querySelectorAll("#viewToggle button").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === view));
  document.getElementById("mapView").hidden = view !== "map";
  document.getElementById("tableView").hidden = view !== "table";
  if (view === "map") document.getElementById("viewBarRight").innerHTML = "";
  renderAll();
  if (view === "map" && map) setTimeout(() => map.invalidateSize(), 50);
}

function renderAll() {
  state.page = 0;   // filter/view changes reset paging; sort & pager call renderTable directly
  renderStats();
  if (state.view === "table") { renderTable(); return; }
  renderMap();
  renderChart();
}

// --------------------------------------------------------------- controls ---
function syncControls() {
  const lvl = state.level === "houses" ? "district" : state.level; // houses drills from district
  document.querySelectorAll("#levelToggle button").forEach((b) =>
    b.classList.toggle("active", b.dataset.level === lvl));
  document.getElementById("metricSelect").value = state.metric;
  document.getElementById("regionScope").value = state.scopeRegion;
  buildCityScope();
  document.getElementById("cityScope").value = state.scopeCity;
}

function buildCityScope() {
  const sel = document.getElementById("cityScope");
  const cities = store.summary.cities.filter(
    (c) => !state.scopeRegion || c.regionId === Number(state.scopeRegion));
  sel.innerHTML = '<option value="">All cities</option>'
    + cities.map((c) => `<option value="${c.cityCode}">${c.nameEn}</option>`).join("");
}

function buildTagChips() {
  const groups = {
    parkingPresence: "Parking", parkingType: "Parking type",
    managementOrg: "Management", elevator: "Elevator",
  };
  const byDim = {};
  for (const tag of store.summary.tags) {
    const [dim] = tagInfo(tag.slug);
    (byDim[dim] = byDim[dim] || []).push(tag);
  }
  const host = document.getElementById("tagGroups");
  host.innerHTML = "";
  for (const dim of Object.keys(groups)) {
    if (!byDim[dim]) continue;
    const wrap = document.createElement("div");
    wrap.className = "tagGroup";
    wrap.innerHTML = `<div class="tagGroupName">${groups[dim]}</div>`;
    const chips = document.createElement("div");
    chips.className = "chips";
    for (const tag of byDim[dim]) {
      const c = document.createElement("button");
      c.className = "chip";
      c.textContent = tag.labelEn.replace(/^Parking: /, "");
      c.onclick = () => {
        state.tags.has(tag.slug) ? state.tags.delete(tag.slug) : state.tags.add(tag.slug);
        c.classList.toggle("active");
        renderAll();
      };
      chips.appendChild(c);
    }
    wrap.appendChild(chips);
    host.appendChild(wrap);
  }
}

// Manual navigation (toggles/dropdowns) is a fresh start: drop the drill stack
// and the individual-house scope, then refit the map to the chosen scope.
function clearDrill() { state.scopeDistrict = ""; viewStack.length = 0; }

function wireControls() {
  document.getElementById("levelToggle").onclick = (e) => {
    if (!e.target.dataset.level) return;
    state.level = e.target.dataset.level; clearDrill(); syncControls(); renderAll(); fitToScope();
  };
  document.getElementById("metricSelect").onchange = (e) => { state.metric = e.target.value; renderAll(); };
  document.getElementById("regionScope").onchange = (e) => {
    state.scopeRegion = e.target.value; state.scopeCity = ""; clearDrill();
    if (state.level === "houses") state.level = "district";
    syncControls(); renderAll(); fitToScope();
  };
  document.getElementById("cityScope").onchange = (e) => {
    state.scopeCity = e.target.value;
    if (state.scopeCity) { const c = store.cityByCode.get(state.scopeCity); if (c) state.scopeRegion = String(c.regionId); }
    clearDrill();
    if (state.level === "houses") state.level = "district";
    syncControls(); renderAll(); fitToScope();
  };
  document.getElementById("resetBtn").onclick = () => {
    state.scopeRegion = ""; state.scopeCity = ""; state.tags.clear(); clearDrill();
    state.excludeFlags.clear(); state.winsorize = false; state.minN = 1;
    state.yearFrom = DEFAULT_YEAR_FROM; state.yearTo = null;
    if (state.level === "houses") state.level = "district";
    document.querySelectorAll(".chip.active").forEach((c) => c.classList.remove("active"));
    document.getElementById("winsorize").checked = false;
    document.getElementById("minN").value = 1; document.getElementById("minNVal").textContent = "1";
    document.getElementById("yearFrom").value = DEFAULT_YEAR_FROM ?? ""; document.getElementById("yearTo").value = "";
    syncControls(); renderAll(); fitToScope();
  };
  document.getElementById("viewToggle").onclick = (e) => {
    if (!e.target.dataset.view) return;
    setView(e.target.dataset.view);
  };
  document.getElementById("chartCollapse").onclick = () => {
    document.getElementById("chartPanel").classList.toggle("collapsed");
    setTimeout(() => { map.invalidateSize(); if (chart) chart.resize(); }, 60);
  };
  // Esc zooms back out to the previous view (map view only).
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && state.view === "map") { e.preventDefault(); popView(); }
  });
}

function wireStatControls() {
  const minN = document.getElementById("minN");
  minN.oninput = () => { state.minN = Number(minN.value); document.getElementById("minNVal").textContent = minN.value; renderAll(); };
  document.getElementById("yearFrom").onchange = (e) => { state.yearFrom = e.target.value ? Number(e.target.value) : null; renderAll(); };
  document.getElementById("yearTo").onchange = (e) => { state.yearTo = e.target.value ? Number(e.target.value) : null; renderAll(); };
  document.getElementById("dealChips").onclick = (e) => {
    const b = e.target.closest("button"); if (!b) return;
    const f = b.dataset.flag;
    state.excludeFlags.has(f) ? state.excludeFlags.delete(f) : state.excludeFlags.add(f);
    b.classList.toggle("active");
    renderAll();
  };
  document.getElementById("winsorize").onchange = (e) => { state.winsorize = e.target.checked; renderAll(); };
  document.getElementById("fixedScale").onchange = (e) => { state.fixedScale = e.target.checked; renderAll(); };
  document.getElementById("lisaMode").onchange = (e) => { state.colorMode = e.target.checked ? "lisa" : "metric"; renderAll(); };
  document.getElementById("methodsBtn").onclick = () => { buildMethodsPanel(); document.getElementById("methodsModal").hidden = false; };
  document.getElementById("methodsClose").onclick = () => { document.getElementById("methodsModal").hidden = true; };
  document.getElementById("methodsModal").addEventListener("click", (e) => { if (e.target.id === "methodsModal") e.currentTarget.hidden = true; });
}

function populateYearControls() {
  const years = [...new Set(store.records.map((r) => r.saleYear).filter(Boolean))].sort((a, b) => a - b);
  // Default the map/stats to a recent window (last two years) so headline numbers
  // aren't a nominal median pooled across ~a decade. The time chart still shows all.
  DEFAULT_YEAR_FROM = years.length >= 2 ? years[years.length - 2] : (years[0] || null);
  state.yearFrom = DEFAULT_YEAR_FROM;
  const opts = (first) => `<option value="">${first}</option>` + years.map((y) => `<option value="${y}">${y}</option>`).join("");
  document.getElementById("yearFrom").innerHTML = opts("earliest");
  document.getElementById("yearTo").innerHTML = opts("latest");
  document.getElementById("yearFrom").value = DEFAULT_YEAR_FROM ?? "";
}

function buildMethodsPanel() {
  const s = store.summary;
  const present = (m) => (100 * (m.n - m.missing) / m.n).toFixed(1) + "%";
  let html = `<h2>Methods &amp; data quality</h2>`
    + `<p class="muted">Source: Ministry of the Interior — Real Estate Actual Price Registration (實價登錄), open data. All prices are <b>nominal</b> NT$.</p>`;
  html += `<h3>Sampling frame</h3>`
    + `<p class="muted">Transaction dates span ${s.period.minDate} – ${s.period.maxDate}. The registry discloses deals in periodic batches, so the earliest months hold only a few late-registered sales — read monthly <em>counts</em> as a disclosure sample rather than a census, and rely on the medians rather than raw volumes.</p>`;
  html += `<h3>Definitions &amp; handling</h3><ul class="muted">`
    + `<li>All figures are <b>medians</b> (robust to the extreme outliers present); the sidebar shows IQR and a bootstrap 95% CI.</li>`
    + `<li>"Housing" excludes land-only and parking-only transactions.</li>`
    + `<li>Unit price is the government's 單價 (already parking-adjusted); "living size" nets parking out of the transferred area.</li>`
    + `<li>Building age = completion→sale; a new build completing after its sale is treated as age 0.</li></ul>`;
  html += `<h3>Field completeness (sale housing, n=${s.dealFlagCounts.total.toLocaleString()})</h3>`
    + `<p class="muted">Core fields are essentially complete — the few blanks are shown for transparency.</p>`
    + `<table><tr><th>Field</th><th class="num">Present</th></tr>`
    + Object.entries(s.missingness).map(([k, m]) => `<tr><td>${k}</td><td class="num">${present(m)}</td></tr>`).join("") + `</table>`;
  const d = s.dealFlagCounts;
  html += `<h3>Deal quality (sale housing)</h3><table>`
    + `<tr><td>Related-party / special relationship</td><td class="num">${d.relatedPartyDeal.toLocaleString()}</td></tr>`
    + `<tr><td>Unpermitted additions noted</td><td class="num">${d.hasAddition.toLocaleString()}</td></tr>`
    + `<tr><td>Cancelled contracts</td><td class="num">${d.cancelledDeal.toLocaleString()}</td></tr></table>`
    + `<p class="muted">Exclude these from any view with the sidebar "Exclude deals" chips.</p>`;
  if (s.moran) html += `<h3>Spatial clustering</h3><p class="muted">District sale unit price has Moran's I = <b>${s.moran.I}</b> `
    + `(p=${s.moran.p}, n=${s.moran.n}) — strong, significant positive spatial autocorrelation. Tick "Price clusters (LISA)" to map hot/cold spots.</p>`;
  if (s.hedonic) html += `<h3>Hedonic price model</h3><p class="muted">OLS on log(dwelling price, net of parking), sale housing `
    + `(n=${s.hedonic.n.toLocaleString()}, R²=${s.hedonic.r2}). Approx. % effect on price, holding the other factors + building type + city constant `
    + `(parking is the amenity premium, not the parking space's own cost):</p>`
    + `<table><tr><th>Factor</th><th class="num">Effect</th><th class="num">p</th></tr>`
    + s.hedonic.terms.map((t) => `<tr><td>${t.term}</td><td class="num">${t.pctEffect > 0 ? "+" : ""}${t.pctEffect}%</td><td class="num">${t.p}</td></tr>`).join("") + `</table>`;
  document.getElementById("methodsBody").innerHTML = html;
}

// Floating hover descriptions for the transaction-type buttons (rendered on
// document.body so the segmented control's overflow:hidden can't clip them).
function wireTypeTooltips() {
  const tip = document.createElement("div");
  tip.className = "hoverTip";
  tip.hidden = true;
  document.body.appendChild(tip);

  const show = (btn) => {
    const info = TYPE_INFO[btn.dataset.type];
    if (!info) return;
    const n = store.summary.totals[btn.dataset.type];
    tip.innerHTML = `<b>${info.title}</b><span>${info.body}`
      + (n != null ? ` <em>${n.toLocaleString()} records in this release.</em>` : "") + `</span>`;
    tip.hidden = false;
    const r = btn.getBoundingClientRect();
    const width = tip.offsetWidth || 290;
    tip.style.left = Math.max(8, Math.min(r.left, window.innerWidth - width - 8)) + "px";
    tip.style.top = (r.bottom + 8) + "px";
  };
  const hide = () => { tip.hidden = true; };

  document.querySelectorAll("#typeToggle button").forEach((b) => {
    b.addEventListener("mouseenter", () => show(b));
    b.addEventListener("mouseleave", hide);
    b.addEventListener("focus", () => show(b));
    b.addEventListener("blur", hide);
  });
}

// ------------------------------------------------------------------- init ---
function buildRegionScope() {
  document.getElementById("regionScope").innerHTML = '<option value="">All regions</option>'
    + store.summary.regions.map((r) => `<option value="${r.regionId}">${r.nameEn}</option>`).join("");
}

function indexGeometry(fc, mapObj, keyProp) {
  for (const f of fc.features) mapObj.set(f.properties[keyProp], f);
}

async function loadData() {
  const summary = await (await fetch(DATA + "summary.json" + DATA_V)).json();
  store.summary = summary;
  summary.cities.forEach((c) => store.cityByCode.set(c.cityCode, c));
  summary.regions.forEach((r) => store.regionById.set(r.regionId, r));
  summary.districts.forEach((d) => store.districtById.set(d.districtId, d));

  const [rg, cg, dg] = await Promise.all([
    fetch(DATA + "regionAggregates.geojson" + DATA_V).then((r) => r.json()),
    fetch(DATA + "cityAggregates.geojson" + DATA_V).then((r) => r.json()),
    fetch(DATA + "districtAggregates.geojson" + DATA_V).then((r) => r.json()),
  ]);
  indexGeometry(rg, store.geom.region, "regionId");
  indexGeometry(cg, store.geom.city, "cityCode");
  indexGeometry(dg, store.geom.district, "districtId");

  const recordSets = await Promise.all(
    summary.cities.map((c) =>
      fetch(DATA + `cityRecords_${c.cityCode}.json` + DATA_V).then((r) => (r.ok ? r.json() : []))));
  summary.cities.forEach((c, i) => {
    for (const r of recordSets[i]) {
      r.cityCode = c.cityCode;
      r.regionId = c.regionId;
      store.records.push(r);
    }
  });
}

function renderHeader() {
  const s = store.summary;
  document.getElementById("subtitle").textContent =
    `Taiwan actual-price registration · transaction dates ${s.period.minDate} → ${s.period.maxDate}`;
  const total = s.totals.sale + s.totals.presale + s.totals.rental;
  document.getElementById("headerStats").innerHTML =
    `<div class="stat"><b>${total.toLocaleString()}</b><span>Transactions</span></div>`
    + `<div class="stat"><b>${s.cities.length}</b><span>Cities/Counties</span></div>`
    + `<div class="stat"><b>${s.districts.length}</b><span>Districts</span></div>`;
  document.getElementById("dataNote").innerHTML =
    "Housing aggregates exclude land-only and parking-only transactions. "
    + "Most volume falls in the current release window; earlier transaction dates "
    + "populate the time series. Source: Ministry of the Interior LVR open data.";
}

async function init() {
  const overlay = document.createElement("div");
  overlay.className = "loadingOverlay";
  overlay.textContent = "Loading Taiwan housing data…";
  document.body.appendChild(overlay);

  // Lock the view to Taiwan (incl. outlying islands) so users can't pan off into
  // an empty world map. (TAIWAN_BOUNDS is a module-level constant.)
  map = L.map("map", {
    preferCanvas: true,
    maxBounds: TAIWAN_BOUNDS,
    maxBoundsViscosity: 1.0,
    minZoom: 7,
    maxZoom: 16,
    // Zoom animation CSS-scales the canvas mid-zoom, making the fixed-size dots
    // appear to grow/shrink with the zoom. Disable it so markers stay put and
    // redraw at their true pixel radius instantly.
    zoomAnimation: false,
  });
  map.fitBounds(TAIWAN_BOUNDS);
  // Clean, English-labelled basemap (no OSM mountain-peak triangles / clutter).
  L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
    attribution: '© OpenStreetMap contributors © CARTO',
    subdomains: "abcd", maxZoom: 19,
  }).addTo(map);

  try {
    await loadData();
    renderHeader();
    buildRegionScope();
    buildTagChips();
    populateYearControls();
    wireControls();
    wireStatControls();
    syncControls();
    renderAll();
    // Containers may have been zero-sized at init; settle layout once loaded.
    setTimeout(() => { map.invalidateSize(); if (chart) chart.resize(); }, 200);
    window.addEventListener("resize", () => { map.invalidateSize(); if (chart) chart.resize(); });
  } catch (err) {
    overlay.textContent = "Failed to load data: " + err.message;
    console.error(err);
    return;
  }
  overlay.remove();
}

init();
