/* Raw SQLite browser — loads the published taiwanHousing.sqlite entirely in the
 * browser with sql.js (SQLite compiled to WebAssembly), so you can page through
 * every table with no backend. Rows stream in as ONE CONTINUOUS SCROLL: an
 * IntersectionObserver appends the next chunk as you approach the bottom. */

const SQLITE_URL = "dataFiles/taiwanHousing.sqlite";
const CDN = "https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.10.3/";
const CHUNK = 200;      // rows appended per step
const SQL_CAP = 5000;   // max rows rendered for an ad-hoc query

let db;
const cur = { table: null, cols: [], total: 0, loaded: 0, loading: false, custom: false };

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const fmt = (v) => (v == null ? '<span class="nullv">NULL</span>'
  : (String(v).length > 140 ? esc(String(v).slice(0, 140)) + "…" : esc(String(v))));
const status = (t) => { const el = $("dbStatus"); el.textContent = t; el.style.display = t ? "block" : "none"; };
const meta = (t) => { $("dbMeta").textContent = t; };

function tableNames() {
  const r = db.exec("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name");
  return r.length ? r[0].values.map((v) => v[0]) : [];
}
const rowCount = (t) => db.exec(`SELECT COUNT(*) FROM "${t}"`)[0].values[0][0];
const columnsOf = (t) => {
  const r = db.exec(`PRAGMA table_info("${t}")`);
  return r.length ? r[0].values.map((v) => v[1]) : [];
};

function buildChips() {
  const host = $("tableChips");
  host.innerHTML = "";
  for (const name of tableNames()) {
    const b = document.createElement("button");
    b.className = "tableChip";
    b.innerHTML = `${name} <span class="chipCount">${rowCount(name).toLocaleString()}</span>`;
    b.onclick = () => {
      document.querySelectorAll(".tableChip").forEach((c) => c.classList.remove("active"));
      b.classList.add("active");
      openTable(name);
    };
    host.appendChild(b);
  }
}

function renderHead(cols) {
  $("dbTable").querySelector("thead").innerHTML =
    "<tr>" + cols.map((c) => `<th>${esc(c)}</th>`).join("") + "</tr>";
}

function openTable(name) {
  cur.table = name; cur.custom = false; cur.loaded = 0; cur.total = rowCount(name); cur.cols = columnsOf(name);
  $("sqlBox").value = `SELECT * FROM ${name}`;
  $("sqlError").textContent = "";
  renderHead(cur.cols);
  $("dbTable").querySelector("tbody").innerHTML = "";
  window.scrollTo(0, 0);
  appendChunk();
}

function appendChunk() {
  if (cur.loading || cur.custom || cur.loaded >= cur.total) return;
  cur.loading = true;
  const r = db.exec(`SELECT * FROM "${cur.table}" LIMIT ${CHUNK} OFFSET ${cur.loaded}`);
  const rows = r.length ? r[0].values : [];
  const html = rows.map((row) => "<tr>" + row.map((c) => `<td>${fmt(c)}</td>`).join("") + "</tr>").join("");
  $("dbTable").querySelector("tbody").insertAdjacentHTML("beforeend", html);
  cur.loaded += rows.length;
  meta(`${cur.table} — showing ${cur.loaded.toLocaleString()} of ${cur.total.toLocaleString()} rows`);
  cur.loading = false;
  // Keep filling until the page is tall enough to scroll (so the observer can take over).
  if (cur.loaded < cur.total && document.body.scrollHeight <= window.innerHeight + 400) appendChunk();
}

function runSql() {
  const sql = $("sqlBox").value.trim();
  if (!sql) return;
  try {
    const r = db.exec(sql);
    cur.custom = true;
    const tbody = $("dbTable").querySelector("tbody");
    document.querySelectorAll(".tableChip").forEach((c) => c.classList.remove("active"));
    if (!r.length) { renderHead([]); tbody.innerHTML = ""; meta("Query OK — 0 rows returned"); $("sqlError").textContent = ""; return; }
    const { columns, values } = r[0];
    renderHead(columns);
    const shown = Math.min(values.length, SQL_CAP);
    tbody.innerHTML = values.slice(0, shown).map((row) =>
      "<tr>" + row.map((c) => `<td>${fmt(c)}</td>`).join("") + "</tr>").join("");
    meta(`Query result — ${values.length.toLocaleString()} rows` + (values.length > shown ? ` (showing first ${shown.toLocaleString()})` : ""));
    $("sqlError").textContent = "";
    window.scrollTo(0, 0);
  } catch (e) {
    $("sqlError").textContent = String(e.message || e);
  }
}

function wire() {
  $("sqlRun").onclick = runSql;
  $("sqlBox").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); runSql(); } });
  $("sqlReset").onclick = () => {
    const active = document.querySelector(".tableChip.active") || document.querySelector(".tableChip");
    if (active) active.click();
  };
  const io = new IntersectionObserver((entries) => { if (entries[0].isIntersecting) appendChunk(); }, { rootMargin: "700px" });
  io.observe($("dbSentinel"));
  // Deterministic backup for the continuous scroll (the observer alone can miss
  // fast jumps / same-tick table switches).
  window.addEventListener("scroll", () => {
    if (window.innerHeight + window.scrollY >= document.body.scrollHeight - 800) appendChunk();
  }, { passive: true });

  // Pin the sticky column headers just below the (variable-height) toolbar.
  const setOffset = () => document.documentElement.style.setProperty("--toolbarH", $("dbToolbar").offsetHeight + "px");
  setOffset();
  window.addEventListener("resize", setOffset);
  new ResizeObserver(setOffset).observe($("dbToolbar"));
}

async function init() {
  try {
    status("Loading SQLite engine…");
    const SQL = await initSqlJs({ locateFile: (f) => CDN + f });
    status("Downloading database (~20 MB, one time)…");
    const res = await fetch(SQLITE_URL);
    if (!res.ok) throw new Error("could not fetch the database (HTTP " + res.status + ")");
    db = new SQL.Database(new Uint8Array(await res.arrayBuffer()));
    status("");
    buildChips();
    wire();
    const first = document.querySelector(".tableChip");
    if (first) first.click();
  } catch (e) {
    status("Failed to load database: " + (e.message || e));
    console.error(e);
  }
}

init();
