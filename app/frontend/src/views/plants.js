import { thumbStyle } from "../store.js";
import { colorOf, esc, fmt, navigate } from "../ui.js";

const PAGE = 50;
const COLS = [
  { key: "plant_id", label: "Plant" },
  { key: "line_id", label: "Line" },
  { key: "position_type", label: "Position" },
  { key: "plant_class", label: "Identification" },
  { key: "identification_confidence", label: "Conf.", num: true, f: (v) => fmt.pct(v) },
  { key: "health_class", label: "Health" },
  { key: "health_score", label: "Score", num: true, f: (v) => fmt.n(v) },
  { key: "canopy_area_m2", label: "Canopy cm²", num: true, f: (v) => fmt.int(v * 1e4) },
  { key: "mean_ndvi", label: "NDVI", num: true, f: (v) => fmt.n(v) },
];

export function renderPlants(el, s, params) {
  const state = {
    q: params.get("q") || "", line: params.get("line") || "", pos: params.get("pos") || "planted",
    cls: params.get("cls") || "", health: params.get("health") || "", canopy: params.get("canopy") || "",
    sort: "plant_id", dir: 1, page: 0,
  };
  const lines = [...new Set(s.list.map((p) => p.line_id).filter(Boolean))].sort();
  el.innerHTML = `
    <div class="page-head">
      <div><div class="eyebrow">Inventory</div><h1>Plants</h1>
        <p>Every detected object with its detection, identification and health results. Click a row for the plant page.</p></div>
      <button class="btn" id="csv">Download CSV</button>
    </div>
    <div class="filters">
      <label class="field">Search<input id="f-q" placeholder="Plant ID, e.g. P01234" value="${esc(state.q)}" /></label>
      <label class="field">Position<select id="f-pos">
        <option value="planted">Planted saplings</option><option value="between">Between-line vegetation</option><option value="">All objects</option></select></label>
      <label class="field">Line<select id="f-line"><option value="">All lines</option>${lines.map((l) => `<option ${l === state.line ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <label class="field">Identification<select id="f-cls"><option value="">Any</option>${s.idClasses.map((c) => `<option ${c.key === state.cls ? "selected" : ""}>${esc(c.key)}</option>`).join("")}</select></label>
      <label class="field">Health<select id="f-health"><option value="">Any</option>${s.healthClasses.map((c) => `<option ${c.key === state.health ? "selected" : ""}>${esc(c.key)}</option>`).join("")}</select></label>
      <label class="field">Canopy<select id="f-canopy"><option value="">Any</option><option value="green">Green canopy</option><option value="none">No green canopy</option></select></label>
    </div>
    <div class="table-wrap"><table class="data"><thead><tr><th style="cursor:default"></th>${COLS.map((c) => `<th data-k="${c.key}">${c.label}</th>`).join("")}</tr></thead><tbody id="rows"></tbody></table></div>
    <div class="pager"><span id="count"></span><span><button class="btn" id="prev">← Previous</button> <button class="btn" id="next">Next →</button></span></div>`;
  el.querySelector("#f-pos").value = state.pos;
  el.querySelector("#f-canopy").value = state.canopy;

  const filtered = () => s.list.filter((p) => {
    if (state.q && !p.plant_id.toLowerCase().includes(state.q.toLowerCase())) return false;
    if (state.pos === "planted" && p.position_type !== "Planting line") return false;
    if (state.pos === "between" && p.position_type === "Planting line") return false;
    if (state.line && p.line_id !== state.line) return false;
    if (state.cls && p.plant_class !== state.cls) return false;
    if (state.health && p.health_class !== state.health) return false;
    if (state.canopy === "green" && !p.living_canopy) return false;
    if (state.canopy === "none" && p.living_canopy) return false;
    return true;
  });

  function draw() {
    const rows = filtered().sort((a, b) => {
      const x = a[state.sort];
      const y = b[state.sort];
      if (x == null) return 1;
      if (y == null) return -1;
      return (x > y ? 1 : x < y ? -1 : 0) * state.dir;
    });
    const pages = Math.max(1, Math.ceil(rows.length / PAGE));
    state.page = Math.min(state.page, pages - 1);
    const view = rows.slice(state.page * PAGE, state.page * PAGE + PAGE);
    el.querySelector("#rows").innerHTML = view.map((p) => `
      <tr data-id="${p.plant_id}">
        <td><span class="thumb" style="${thumbStyle(s, p.plant_id, "rgb", 36)}"></span></td>
        ${COLS.map((c) => {
          const v = p[c.key];
          if (c.key === "plant_class") return `<td><span class="dot" style="background:${colorOf(s.idClasses, v)}"></span> ${esc(v ?? "–")}</td>`;
          if (c.key === "health_class") return `<td>${v ? `<span class="dot" style="background:${colorOf(s.healthClasses, v)}"></span> ${esc(v)}` : '<span class="muted">not graded</span>'}</td>`;
          if (c.key === "position_type") return `<td>${esc(v === "Planting line" ? "Planted" : v)}</td>`;
          return c.num ? `<td class="n">${c.f(v)}</td>` : `<td>${esc(v ?? "–")}</td>`;
        }).join("")}
      </tr>`).join("") || `<tr><td colspan="${COLS.length + 1}" class="empty" style="padding:18px">No plants match these filters.</td></tr>`;
    el.querySelector("#count").textContent = `${fmt.int(rows.length)} plants · page ${state.page + 1} of ${pages}`;
    el.querySelector("#prev").disabled = state.page === 0;
    el.querySelector("#next").disabled = state.page >= pages - 1;
    el.querySelectorAll("th[data-k]").forEach((th) => {
      th.setAttribute("aria-sort", th.dataset.k === state.sort ? (state.dir > 0 ? "ascending" : "descending") : "none");
    });
    return rows;
  }

  const bind = (id, key) => el.querySelector(id).addEventListener("input", (e) => { state[key] = e.target.value; state.page = 0; draw(); });
  bind("#f-q", "q"); bind("#f-pos", "pos"); bind("#f-line", "line"); bind("#f-cls", "cls"); bind("#f-health", "health"); bind("#f-canopy", "canopy");
  el.querySelectorAll("th[data-k]").forEach((th) => th.addEventListener("click", () => {
    state.dir = state.sort === th.dataset.k ? -state.dir : 1;
    state.sort = th.dataset.k;
    draw();
  }));
  el.querySelector("#prev").addEventListener("click", () => { state.page -= 1; draw(); });
  el.querySelector("#next").addEventListener("click", () => { state.page += 1; draw(); });
  el.querySelector("#rows").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-id]");
    if (tr) navigate(`#/plants/${tr.dataset.id}`);
  });
  el.querySelector("#csv").addEventListener("click", () => {
    const rows = draw();
    const keys = ["plant_id", "line_id", "position_type", "lon", "lat", "area_m2", "detection_confidence", "plant_class",
      "identification_confidence", "health_class", "health_score", "health_confidence", "canopy_area_m2", "canopy_status",
      "mean_ndvi", "median_ndvi", "p10_ndvi", "p90_ndvi", "vari", "green_fraction"];
    const csv = [keys.join(",")].concat(rows.map((p) => keys.map((k) => {
      const v = p[k];
      return typeof v === "string" && /[",]/.test(v) ? `"${v.replace(/"/g, '""')}"` : (v ?? "");
    }).join(","))).join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    a.download = `farmwings_${s.summary.project.replace(/\W+/g, "_")}_plants.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  });
  draw();
}
