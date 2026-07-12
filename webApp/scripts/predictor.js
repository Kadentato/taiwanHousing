/* In-browser house-price predictor. Loads the exported gradient-boosted model
 * (dataFiles/predictor.json) and evaluates it entirely client-side: build the
 * feature vector exactly as the Python pipeline does, walk the serialised trees,
 * convert the model's real 2021 NT$ back to nominal, and apply the calibrated
 * conformal band for the 50 / 80 / 95% ranges. No backend. */

let model;
const $ = (id) => document.getElementById(id);
const clamp = (x, lo, hi) => Math.min(Math.max(x, lo), hi);
const pretty = (s) => (s == null ? "" : String(s).replace(/([a-z])([A-Z])/g, "$1 $2").replace(/^./, (c) => c.toUpperCase()));
const fmtM = (v) => "NT$" + (v / 1e6).toFixed(v >= 1e7 ? 1 : 2) + "M";
const fmtUnit = (v) => "NT$" + Math.round(v).toLocaleString() + "/m²";

// House/apartment building types only (this is a home predictor, not factories/offices),
// each with realistic floor bounds so you can't build a 14-storey walk-up.
const RESIDENTIAL = ["residentialTower", "elevatorBuildingLowRise", "walkUpApartment", "townhouse"];
const TYPE_FLOORS = {
  townhouse: { def: 2, max: 5 },   // houses are commonly 1-4 storeys; don't assume 3
  walkUpApartment: { def: 4, max: 6 },
  elevatorBuildingLowRise: { def: 8, max: 12 },
  residentialTower: { def: 13, max: 42 },
};
const TYPE_LABELS = {
  residentialTower: "Apartment tower (11F+, elevator)",
  elevatorBuildingLowRise: "Low-rise apartment + elevator (華廈)",
  walkUpApartment: "Walk-up apartment (公寓, ≤5F)",
  townhouse: "House / townhouse (透天厝)",
};

// Total floors follow the building type; the unit's own floor can't exceed the total.
function applyTypeFloors() {
  const type = $("pType").value;
  const tf = TYPE_FLOORS[type] || { def: 7, max: 30 };
  $("pTotalFloors").max = tf.max;
  $("pTotalFloors").value = tf.def;
  // A standalone house occupies its whole building — there's no "which floor".
  $("floorField").style.display = type === "townhouse" ? "none" : "";
  clampFloor();
}
function clampFloor() {
  const tot = Math.max(1, parseInt($("pTotalFloors").value) || 1);
  $("pFloor").max = tot;
  if ((parseInt($("pFloor").value) || 1) > tot) $("pFloor").value = tot;
}

async function init() {
  model = await (await fetch("dataFiles/predictor.json?v=5")).json();
  const opt = (v, label) => `<option value="${v}">${label}</option>`;
  $("pCity").innerHTML = model.ui.cities.map((c) => opt(c.code, c.name)).join("");
  const types = model.ui.buildingTypes.filter((t) => RESIDENTIAL.includes(t));
  $("pType").innerHTML = (types.length ? types : model.ui.buildingTypes).map((t) => opt(t, TYPE_LABELS[t] || pretty(t))).join("");
  $("pUse").innerHTML = model.ui.mainUses.map((t) => opt(t, pretty(t))).join("");
  $("pMaterial").innerHTML = model.ui.mainMaterials.map((t) => opt(t, pretty(t))).join("");
  const setSel = (id, v) => { const el = $(id); if ([...el.options].some((o) => o.value === v)) el.value = v; };
  setSel("pType", "elevatorBuildingLowRise"); setSel("pUse", "residential"); setSel("pMaterial", "reinforcedConcrete");
  fillDistricts();
  applyTypeFloors();
  $("predAsOf").textContent = "· as of " + model.asOfLabel;

  $("pCity").addEventListener("change", () => { fillDistricts(); compute(); });
  $("pType").addEventListener("change", () => { applyTypeFloors(); compute(); });
  ["pTotalFloors", "pFloor"].forEach((id) => $(id).addEventListener("input", () => { clampFloor(); compute(); }));
  const skip = new Set(["pCity", "pType", "pTotalFloors", "pFloor"]);
  document.querySelectorAll("input, select").forEach((el) => { if (!skip.has(el.id)) el.addEventListener("input", compute); });
  compute();
}

function fillDistricts() {
  const ds = model.ui.districtsByCity[$("pCity").value] || [];
  // Default to a real district (a specific location gives a meaningful estimate;
  // the encoder would otherwise fall back to the national average).
  $("pDistrict").innerHTML = ds.map((d) => `<option value="${d}">${d}</option>`).join("");
}

// Numeric features in NUMERIC order (shared by the price and difficulty models).
function numericFeatures(inp) {
  const n = {
    logArea: Math.log(Math.max(inp.livingAreaPing, 1)),
    logLand: Math.log1p(Math.max(inp.landAreaPing, 0)),
    mainBuildingRatio: clamp(inp.mainBuildingRatio, 0.15, 1),
    buildingAgeYears: inp.buildingAgeYears,
    ageSq: inp.buildingAgeYears * inp.buildingAgeYears,
    bedrooms: inp.bedrooms, livingRooms: inp.livingRooms, bathrooms: inp.bathrooms,
    transferFloor: clamp(inp.transferFloor, 0, 70), totalFloors: clamp(inp.totalFloors, 1, 70),
    floorRatio: 0, hasParking: inp.hasParking, hasElevator: inp.hasElevator,
    hasManagementOrg: inp.hasManagementOrg, hasCompartments: inp.hasCompartments,
    monthIndex: model.asOfMonthIndex,
  };
  n.floorRatio = clamp(n.transferFloor / n.totalFloors, 0, 1.5);
  return model.numericOrder.map((k) => n[k]);
}

// Append CATCOLS encodings (price model and difficulty model use different encoders).
function withCats(numArr, inp, encoders) {
  const fv = numArr.slice();
  for (const c of model.catOrder) {
    const e = encoders[c], v = inp[c];
    fv.push(v != null && e.map[v] != null ? e.map[v] : e.global);
  }
  return fv;
}

// Walk serialised trees: leaf = [1, value]; split = [0, feat, thr, left, right, missingLeft].
function walk(fv, trees, baseline) {
  let s = baseline;
  for (const tree of trees) {
    let n = 0;
    for (;;) {
      const nd = tree[n];
      if (nd[0] === 1) { s += nd[1]; break; }
      const x = fv[nd[1]];
      n = (x == null || Number.isNaN(x)) ? (nd[5] ? nd[3] : nd[4]) : (x <= nd[2] ? nd[3] : nd[4]);
    }
  }
  return s;
}

function num(id, def) { const v = parseFloat($(id).value); return Number.isFinite(v) ? v : def; }

function compute() {
  const d = model.defaults;
  const tot = num("pTotalFloors", d.totalFloors);
  const isHouse = $("pType").value === "townhouse";
  const inp = {
    cityCode: $("pCity").value, districtEn: $("pDistrict").value,
    // size inputs are in m²; the model works in ping
    livingAreaPing: num("pSize", 100) / model.m2PerPing, buildingAgeYears: num("pAge", d.buildingAgeYears),
    // A house has no single "unit floor" (you own the whole building), and in the LVR training
    // data townhouses carry no transfer-floor at all — so use the model's default rather than
    // pinning it to the top storey ("owns it all"), which the model was never trained on.
    transferFloor: isHouse ? d.transferFloor : num("pFloor", d.transferFloor), totalFloors: tot,
    bedrooms: num("pBeds", d.bedrooms), bathrooms: num("pBaths", d.bathrooms),
    livingRooms: num("pLiving", d.livingRooms), landAreaPing: num("pLandPing", 16) / model.m2PerPing,
    mainBuildingRatio: num("pMainRatio", d.mainBuildingRatio),
    buildingType: $("pType").value, mainUse: $("pUse").value, mainMaterial: $("pMaterial").value,
    hasParking: $("pParking").checked ? 1 : 0, hasElevator: $("pElevator").checked ? 1 : 0,
    hasManagementOrg: $("pMgmt").checked ? 1 : 0, hasCompartments: 1,
  };
  const numFeats = numericFeatures(inp);
  const logReal = walk(withCats(numFeats, inp, model.encoders), model.trees, model.baseline);
  // Adaptive band: a difficulty model sigma(x) scales the width per property
  // (tight for standard homes, wide for oddballs). Falls back to the global band.
  const sig = model.sigma
    ? Math.max(walk(withCats(numFeats, inp, model.sigma.encoders), model.sigma.trees, model.sigma.baseline),
               model.sigma.floor)
    : null;
  const clip = (p) => clamp(p, model.priceLo, model.priceHi);
  const f = model.nominalFactor;
  const areaM2 = inp.livingAreaPing * model.m2PerPing;
  const unit = clip(Math.exp(logReal)) * f;
  const band = (lvl) => {
    const half = sig != null ? model.sigma.levels[String(lvl)] * sig : model.levels[String(lvl)];
    return [clip(Math.exp(logReal - half)) * f, clip(Math.exp(logReal + half)) * f];
  };
  render(unit, areaM2, { 50: band(50), 80: band(80), 95: band(95) }, sig != null);
}

function render(unit, areaM2, bands, adaptive) {
  const totalPoint = unit * areaM2;
  $("predBig").textContent = fmtM(totalPoint);
  $("predUnit").textContent = fmtUnit(unit) + " · " + Math.round(areaM2) + " m²";

  const lo = bands[95][0] * areaM2, hi = bands[95][1] * areaM2, span = Math.max(hi - lo, 1);
  const pos = (v) => (100 * (v * areaM2 - lo) / span).toFixed(1) + "%";
  const wid = (b) => (100 * (bands[b][1] - bands[b][0]) * areaM2 / span).toFixed(1) + "%";
  const left = (b) => (100 * (bands[b][0] * areaM2 - lo) / span).toFixed(1) + "%";

  const meta = { 50: "most likely", 80: "reasonable", 95: "high-confidence" };
  const list = [50, 80, 95].map((b) =>
    `<div class="rangeVals"><span><b>${b}%</b> ${meta[b]}</span>`
    + `<span>${fmtM(bands[b][0] * areaM2)} – ${fmtM(bands[b][1] * areaM2)}</span></div>`).join("");

  $("predRanges").innerHTML =
    `<div class="rangeRow"><div class="rangeBarWrap">`
    + `<div class="rangeBar b95" style="left:0;right:0"></div>`
    + `<div class="rangeBar b80" style="left:${left(80)};width:${wid(80)}"></div>`
    + `<div class="rangeBar b50" style="left:${left(50)};width:${wid(50)}"></div>`
    + `<div style="position:absolute;top:-3px;bottom:-3px;width:2px;background:#0f172a;left:${pos(unit)}"></div>`
    + `</div></div>` + list;

  $("predNote").innerHTML =
    "Gradient-boosted model on ~3.3M sales (2012–2026), cross-validated out-of-time. The <b>range</b> is a "
    + (adaptive
      ? "<b>locally-weighted</b> conformal interval — its width is tailored to this property, so a standard "
        + "home in a data-rich area gets a tighter band and an unusual one a wider band, "
      : "conformal prediction interval, ")
    + "calibrated so the stated share of real sales fall inside it. Prices are nominal NT$. Renovation, exact "
    + "street/MRT location and negotiation aren't in the open registry, so a single home carries genuine "
    + "uncertainty — read the 80% band as the practical range.";
}

init();
