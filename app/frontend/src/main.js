import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
// MapLibre v6 loads its web worker as a separate module; let Vite bundle it.
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import "./style.css";
import { barChart, histogram, hideTip } from "./charts.js";

maplibregl.setWorkerUrl(workerUrl);

// ---------------------------------------------------------------- constants
const DATA = (p) => new URL(`data/${p}`, document.baseURI).href;

const CSS = getComputedStyle(document.documentElement);
const css = (v) => CSS.getPropertyValue(v).trim();

const ID_CLASSES = [
  { key: "Palm (planted)", color: css("--palm") },
  { key: "Other vegetation", color: css("--other") },
  { key: "Unclassified", color: css("--uncl") },
];
const HEALTH_CLASSES = [
  { key: "Very high vigour", color: css("--h-vhigh") },
  { key: "High vigour", color: css("--h-high") },
  { key: "Moderate vigour", color: css("--h-mod") },
  { key: "Low vigour", color: css("--h-low") },
  { key: "Very low vigour", color: css("--h-vlow") },
];
const LOW = ["Low vigour", "Very low vigour"];
const POLY_MINZOOM = 19.2; // below this, plants are drawn as centroid points

const state = {
  summary: null,
  plants: null, // FeatureCollection (polygons)
  byId: new Map(),
  layers: { rgb: true, ndvi: false, vegmask: false, lines: false, detected: true, between: false,
    ident: false, unclassified: false, health: false, lowhealth: false },
  selection: null, // polygon ring [[lon,lat],...]
  selected: null, // plant_id
};

const fmt = {
  int: (v) => (v == null ? "–" : Math.round(v).toLocaleString("en-US")),
  pct: (v, d = 0) => (v == null ? "–" : `${(v * 100).toFixed(d)}%`),
  n: (v, d = 2) => (v == null ? "–" : Number(v).toFixed(d)),
};

// ---------------------------------------------------------------- tile packs
// Tiles are packed into chunk files (see processing/make_tiles.py); this protocol
// serves "fw://<layer>/<z>/<x>/<y>" from them, fetching each chunk once.
const EMPTY_PNG = Uint8Array.from(atob(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="), (c) => c.charCodeAt(0)).buffer;
let tileIndex = null;
const chunkCache = new Map();
function loadChunk(file) {
  if (!chunkCache.has(file)) {
    chunkCache.set(file, fetch(DATA(`tiles/${file}`)).then((r) => {
      if (!r.ok) throw new Error(`tile pack ${file}: ${r.status}`);
      return r.arrayBuffer();
    }));
  }
  return chunkCache.get(file);
}
maplibregl.addProtocol("fw", async (params) => {
  const [layer, z, x, y] = params.url.replace("fw://", "").split("/");
  const L = tileIndex?.layers?.[layer];
  const hit = L?.tiles[`${z}/${x}/${y}`];
  if (!hit) return { data: EMPTY_PNG.slice(0) };
  const buf = await loadChunk(L.files[hit[0]]);
  return { data: buf.slice(hit[1], hit[1] + hit[2]) };
});

// ---------------------------------------------------------------- boot
async function boot() {
  const [summary, plants, lines, index] = await Promise.all([
    fetch(DATA("summary.json")).then((r) => r.json()),
    fetch(DATA("plants.json")).then((r) => r.json()),
    fetch(DATA("lines.json")).then((r) => r.json()),
    fetch(DATA("tiles/index.json")).then((r) => r.json()),
  ]);
  state.summary = summary;
  state.plants = plants;
  tileIndex = index;
  for (const f of plants.features) state.byId.set(f.properties.plant_id, f.properties);
  const points = {
    type: "FeatureCollection",
    features: plants.features.map((f) => ({
      type: "Feature", properties: f.properties,
      geometry: { type: "Point", coordinates: [f.properties.lon, f.properties.lat] },
    })),
  };

  renderHeader(summary);
  renderKpis(summary);
  renderAnalytics();
  renderModels(summary);
  initMap(index, plants, points, lines);
  initTabs();
  initLayerPanel();
  renderLegend();
}

// ---------------------------------------------------------------- header / KPIs
function renderHeader(s) {
  document.getElementById("project-meta").textContent =
    `Processed ${s.processing_date} · ${s.stats.surveyed_area_ha.toFixed(2)} ha surveyed · species declared: palm`;
  const ol = document.getElementById("lifecycle");
  ol.innerHTML = s.lifecycle.map((st) => {
    const icon = st.status === "done" ? "✓" : st.status === "partial" ? "◐" : "✕";
    return `<li class="${st.status}" title="${st.detail}"><span class="st">${icon}</span>${st.step}${st.status === "partial" ? " · partial" : ""}</li>`;
  }).join("");
}

function renderKpis(s) {
  const t = s.stats;
  const hc = t.health_counts_planted;
  const cards = [
    { label: "Planted positions", value: fmt.int(t.planted_positions), note: `on ${t.planting_lines} drip lines · ${fmt.int(t.planted_density_per_ha)}/ha` },
    { label: "Identified as palm", value: fmt.int(t.palms_identified), note: "Palm (planted) class · experimental" },
    { label: "Identification rate", value: fmt.pct(t.identification_rate), note: `${fmt.int(t.identified)} of ${fmt.int(t.all_detections)} detections` },
    { label: "Plant classes", value: Object.keys(t.class_counts).filter((k) => k !== "Unclassified").length, note: "Palm (planted) · Other vegetation" },
    { label: "High vigour", value: fmt.pct(t.high_vigour_share_planted), note: `${fmt.int(t.high_vigour_planted)} planted · relative index` },
    { label: "Low vigour", value: fmt.int(t.low_vigour_planted), note: `${fmt.int(hc["Very low vigour"] || 0)} very low · potential stress` },
    { label: "Avg health score", value: fmt.n(t.avg_health_score_planted), note: "0–1, relative to this field" },
    { label: "Mean NDVI (plants)", value: fmt.n(t.avg_mean_ndvi_planted), note: `soil ${fmt.n(t.field_soil_ndvi_median)}` },
  ];
  document.getElementById("kpis").innerHTML = cards.map((c) =>
    `<div class="kpi"><div class="label">${c.label}</div><div class="value">${c.value}</div><div class="note">${c.note}</div></div>`).join("");
}

// ---------------------------------------------------------------- map
let map;
function initMap(index, plants, points, lines) {
  const b = index.layers.rgb.bounds;
  const raster = (name, maxz) => ({
    type: "raster", tiles: [`fw://${name}/{z}/{x}/{y}`], tileSize: 256,
    minzoom: index.layers[name].minzoom, maxzoom: maxz ?? index.layers[name].maxzoom, bounds: b,
  });
  map = new maplibregl.Map({
    container: "map",
    style: {
      version: 8,
      sources: {
        rgb: { ...raster("rgb"), attribution: "Imagery: Hari Pilot drone survey" },
        ndvi: raster("ndvi"), vegmask: raster("vegmask"),
        plants: { type: "geojson", data: plants, promoteId: "plant_id" },
        points: { type: "geojson", data: points, promoteId: "plant_id" },
        lines: { type: "geojson", data: lines },
        selection: { type: "geojson", data: emptyFC() },
        draft: { type: "geojson", data: emptyFC() },
      },
      layers: [{ id: "bg", type: "background", paint: { "background-color": "#0b0c0e" } }],
    },
    bounds: [[b[0], b[1]], [b[2], b[3]]],
    fitBoundsOptions: { padding: 30 },
    minZoom: 16, maxZoom: 24.5,
    attributionControl: { compact: true },
  });
  window.farmwings = { map, state }; // handle for debugging from the console
  map.addControl(new maplibregl.NavigationControl({ showCompass: true }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");
  map.on("load", () => {
    addLayers();
    applyLayerVisibility();
    document.getElementById("loading").hidden = true;
  });
  map.on("error", (e) => console.warn(e.error?.message || e));
  initDraw();
}

const emptyFC = () => ({ type: "FeatureCollection", features: [] });

function matchColor(field, classes, fallback = "#888") {
  return ["match", ["get", field], ...classes.flatMap((c) => [c.key, c.color]), fallback];
}

function addLayers() {
  map.addLayer({ id: "rgb", type: "raster", source: "rgb", paint: { "raster-fade-duration": 150 } });
  map.addLayer({ id: "ndvi", type: "raster", source: "ndvi", paint: { "raster-opacity": 0.95, "raster-resampling": "nearest" } });
  map.addLayer({ id: "vegmask", type: "raster", source: "vegmask", paint: { "raster-opacity": 0.9 } });
  map.addLayer({ id: "lines", type: "line", source: "lines",
    paint: { "line-color": "#f4f4f0", "line-opacity": 0.55, "line-width": ["interpolate", ["linear"], ["zoom"], 17, 0.5, 22, 1.5],
      "line-dasharray": [3, 2] } });

  const planted = ["==", ["get", "position_type"], "Planting line"];
  const between = ["!=", ["get", "position_type"], "Planting line"];
  const radius = ["interpolate", ["exponential", 2], ["zoom"], 16, 0.5, 17, 0.8, 18, 1.8, 19.2, 4.5];

  // 2 - identification (fills)
  map.addLayer({ id: "ident-fill", type: "fill", source: "plants", minzoom: POLY_MINZOOM,
    paint: { "fill-color": matchColor("plant_class", ID_CLASSES), "fill-opacity": 0.6 } });
  map.addLayer({ id: "ident-pt", type: "circle", source: "points", maxzoom: POLY_MINZOOM,
    paint: { "circle-color": matchColor("plant_class", ID_CLASSES), "circle-radius": radius, "circle-opacity": 0.95 } });
  // 3 - health (fills)
  map.addLayer({ id: "health-fill", type: "fill", source: "plants", minzoom: POLY_MINZOOM,
    paint: { "fill-color": matchColor("health_class", HEALTH_CLASSES), "fill-opacity": 0.7 } });
  map.addLayer({ id: "health-pt", type: "circle", source: "points", maxzoom: POLY_MINZOOM,
    paint: { "circle-color": matchColor("health_class", HEALTH_CLASSES), "circle-radius": radius, "circle-opacity": 0.95 } });

  // 1 - detection (outlines / points)
  map.addLayer({ id: "detected-line", type: "line", source: "plants", minzoom: POLY_MINZOOM, filter: planted,
    paint: { "line-color": css("--detect"), "line-width": ["interpolate", ["linear"], ["zoom"], 19, 1, 23, 2.2] } });
  map.addLayer({ id: "detected-pt", type: "circle", source: "points", maxzoom: POLY_MINZOOM, filter: planted,
    paint: { "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": css("--detect"), "circle-stroke-width": 1.2, "circle-radius": radius } });
  map.addLayer({ id: "between-line", type: "line", source: "plants", minzoom: POLY_MINZOOM, filter: between,
    paint: { "line-color": css("--between"), "line-width": ["interpolate", ["linear"], ["zoom"], 19, 1, 23, 2], "line-dasharray": [2, 1] } });
  map.addLayer({ id: "between-pt", type: "circle", source: "points", maxzoom: POLY_MINZOOM, filter: between,
    paint: { "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": css("--between"), "circle-stroke-width": 1.2, "circle-radius": radius } });

  // extra analytical layers
  map.addLayer({ id: "unclassified-line", type: "line", source: "plants", minzoom: POLY_MINZOOM,
    filter: ["==", ["get", "plant_class"], "Unclassified"],
    paint: { "line-color": "#ffffff", "line-width": 2.2, "line-dasharray": [1.5, 1] } });
  map.addLayer({ id: "unclassified-pt", type: "circle", source: "points", maxzoom: POLY_MINZOOM,
    filter: ["==", ["get", "plant_class"], "Unclassified"],
    paint: { "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": "#ffffff", "circle-stroke-width": 1.6,
      "circle-radius": ["interpolate", ["exponential", 2], ["zoom"], 16, 2.5, 19.2, 7] } });
  map.addLayer({ id: "lowhealth-line", type: "line", source: "plants", minzoom: POLY_MINZOOM,
    filter: ["in", ["get", "health_class"], ["literal", LOW]],
    paint: { "line-color": css("--h-vlow"), "line-width": 2.6 } });
  map.addLayer({ id: "lowhealth-pt", type: "circle", source: "points", maxzoom: POLY_MINZOOM,
    filter: ["in", ["get", "health_class"], ["literal", LOW]],
    paint: { "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": css("--h-vlow"), "circle-stroke-width": 1.8,
      "circle-radius": ["interpolate", ["exponential", 2], ["zoom"], 16, 2.5, 19.2, 7] } });

  // selection + draft + selected plant
  map.addLayer({ id: "selection-fill", type: "fill", source: "selection", paint: { "fill-color": "#4cc38a", "fill-opacity": 0.08 } });
  map.addLayer({ id: "selection-line", type: "line", source: "selection", paint: { "line-color": "#4cc38a", "line-width": 2 } });
  map.addLayer({ id: "draft-line", type: "line", source: "draft", paint: { "line-color": "#4cc38a", "line-width": 2, "line-dasharray": [2, 1] } });
  map.addLayer({ id: "draft-pt", type: "circle", source: "draft", filter: ["==", ["geometry-type"], "Point"],
    paint: { "circle-color": "#4cc38a", "circle-radius": 4, "circle-stroke-color": "#0b0c0e", "circle-stroke-width": 1.5 } });
  map.addLayer({ id: "selected-ring", type: "line", source: "plants", filter: ["==", ["get", "plant_id"], ""],
    paint: { "line-color": "#ffffff", "line-width": 3 } });
  map.addLayer({ id: "selected-pt", type: "circle", source: "points", filter: ["==", ["get", "plant_id"], ""],
    paint: { "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": "#ffffff", "circle-stroke-width": 2.5, "circle-radius": 9 } });

  // interaction
  const clickable = ["ident-fill", "ident-pt", "health-fill", "health-pt", "detected-line", "detected-pt", "between-line",
    "between-pt", "unclassified-line", "unclassified-pt", "lowhealth-line", "lowhealth-pt"];
  map.on("click", (e) => {
    if (drawMode) return;
    const pad = 6;
    const feats = map.queryRenderedFeatures([[e.point.x - pad, e.point.y - pad], [e.point.x + pad, e.point.y + pad]],
      { layers: clickable.filter((l) => map.getLayer(l) && map.getLayoutProperty(l, "visibility") !== "none") });
    let pid = feats[0]?.properties?.plant_id;
    if (!pid && map.getZoom() >= POLY_MINZOOM) {
      // clicking inside a polygon whose only visible layer is an outline
      pid = pointInPlant(e.lngLat);
    }
    if (pid) selectPlant(pid);
  });
  map.on("mousemove", (e) => {
    if (drawMode) return;
    const f = map.queryRenderedFeatures(e.point, { layers: clickable.filter((l) => map.getLayer(l) && map.getLayoutProperty(l, "visibility") !== "none") });
    map.getCanvas().style.cursor = f.length ? "pointer" : "";
  });
}

function pointInPlant(lngLat) {
  const p = [lngLat.lng, lngLat.lat];
  for (const f of state.plants.features) {
    const pr = f.properties;
    if (Math.abs(pr.lon - p[0]) > 2e-5 || Math.abs(pr.lat - p[1]) > 2e-5) continue;
    if (!layerAllows(pr)) continue;
    if (pip(p, f.geometry.coordinates[0])) return pr.plant_id;
  }
  return null;
}

// Plants currently in scope: between-line vegetation only when its layer is on
function layerAllows(p) {
  return state.layers.between || p.position_type === "Planting line";
}

function applyLayerVisibility() {
  if (!map?.getLayer("rgb")) return;
  const L = state.layers;
  const vis = (ids, on) => ids.forEach((id) => map.getLayer(id) && map.setLayoutProperty(id, "visibility", on ? "visible" : "none"));
  vis(["rgb"], L.rgb);
  vis(["ndvi"], L.ndvi);
  vis(["vegmask"], L.vegmask);
  vis(["lines"], L.lines);
  vis(["detected-line", "detected-pt"], L.detected);
  vis(["between-line", "between-pt"], L.between);
  vis(["ident-fill", "ident-pt"], L.ident);
  vis(["health-fill", "health-pt"], L.health);
  vis(["unclassified-line", "unclassified-pt"], L.unclassified);
  vis(["lowhealth-line", "lowhealth-pt"], L.lowhealth);
  // Identification / health / extra layers cover between-line vegetation only when it is switched on
  const scope = L.between ? null : ["==", ["get", "position_type"], "Planting line"];
  const withScope = (f) => (scope ? (f ? ["all", scope, f] : scope) : f ?? null);
  for (const id of ["ident-fill", "ident-pt", "health-fill", "health-pt"]) map.setFilter(id, withScope(null));
  for (const id of ["unclassified-line", "unclassified-pt"]) map.setFilter(id, withScope(["==", ["get", "plant_class"], "Unclassified"]));
  for (const id of ["lowhealth-line", "lowhealth-pt"]) map.setFilter(id, withScope(["in", ["get", "health_class"], ["literal", LOW]]));
}

// ---------------------------------------------------------------- layer panel + legend
function initLayerPanel() {
  document.querySelectorAll("input[data-layer]").forEach((inp) => {
    inp.checked = !!state.layers[inp.dataset.layer];
    inp.addEventListener("change", () => {
      const k = inp.dataset.layer;
      state.layers[k] = inp.checked;
      // The two analytical fill layers are shown one at a time so they are never confused
      if (inp.checked && (k === "ident" || k === "health")) {
        const other = k === "ident" ? "health" : "ident";
        state.layers[other] = false;
        document.querySelector(`input[data-layer="${other}"]`).checked = false;
      }
      applyLayerVisibility();
      renderLegend();
      renderAnalytics();
      if (state.selection) renderSelection();
    });
  });
  const tgl = document.getElementById("layers-toggle");
  tgl.addEventListener("click", () => {
    const body = document.getElementById("layers-body");
    const open = body.hidden;
    body.hidden = !open;
    tgl.setAttribute("aria-expanded", String(open));
    tgl.textContent = open ? "▾" : "▸";
  });
}

function scopedPlants() {
  return state.plants.features.map((f) => f.properties).filter(layerAllows);
}

function countBy(list, key) {
  const m = {};
  for (const p of list) m[p[key]] = (m[p[key]] || 0) + 1;
  return m;
}

function renderLegend() {
  const L = state.layers;
  const plants = scopedPlants();
  const parts = [];
  if (L.detected || L.between) {
    const all = state.plants.features.map((f) => f.properties);
    const nP = all.filter((p) => p.position_type === "Planting line").length;
    parts.push(`<div><h4>Detected Plants</h4>
      ${L.detected ? `<div class="row"><span class="sw ring" style="border-color:${css("--detect")}"></span>Planted position (on drip line)<span class="count">${fmt.int(nP)}</span></div>` : ""}
      ${L.between ? `<div class="row"><span class="sw ring" style="border-color:${css("--between")};border-style:dashed"></span>Between-line vegetation<span class="count">${fmt.int(all.length - nP)}</span></div>` : ""}
      <div class="note">Polygons from zoom 19; points below.</div></div>`);
  }
  if (L.ident) {
    const c = countBy(plants, "plant_class");
    parts.push(`<div><h4>Plant Identification</h4>${ID_CLASSES.map((k) =>
      `<div class="row"><span class="sw" style="background:${k.color}"></span>${k.key}<span class="count">${fmt.int(c[k.key] || 0)}</span></div>`).join("")}
      <div class="note">Unclassified = confidence below ${Math.round(state.summary.models.identification.min_confidence * 100)}%.</div></div>`);
  }
  if (L.health) {
    const c = countBy(plants, "health_class");
    parts.push(`<div><h4>Plant Health · relative vigour</h4>${HEALTH_CLASSES.map((k) =>
      `<div class="row"><span class="sw" style="background:${k.color}"></span>${k.key}<span class="count">${fmt.int(c[k.key] || 0)}</span></div>`).join("")}
      <div class="note">Relative to this field; not a disease diagnosis.</div></div>`);
  }
  if (L.lowhealth) parts.push(`<div class="row"><span class="sw ring" style="border-color:${css("--h-vlow")}"></span>Low / very low vigour<span class="count">${fmt.int(plants.filter((p) => LOW.includes(p.health_class)).length)}</span></div>`);
  if (L.unclassified) parts.push(`<div class="row"><span class="sw ring" style="border-color:#fff;border-style:dashed"></span>Unclassified<span class="count">${fmt.int(plants.filter((p) => p.plant_class === "Unclassified").length)}</span></div>`);
  if (L.ndvi) {
    const [lo, hi] = tileIndex.ndvi_range;
    parts.push(`<div><h4>NDVI</h4><div class="ramp" style="background:linear-gradient(90deg,#a50026,#f46d43,#fee08b,#d9ef8b,#66bd63,#006837)"></div>
      <div class="ramp-labels"><span>≤ ${lo}</span><span>${((lo + hi) / 2).toFixed(2)}</span><span>≥ ${hi}</span></div>
      <div class="note">Bare soil ≈ ${fmt.n(state.summary.stats.field_soil_ndvi_median)}</div></div>`);
  }
  if (L.vegmask) parts.push(`<div class="row"><span class="sw" style="background:#14e678"></span>Vegetation mask: NDVI clearly above local soil</div>`);
  if (L.lines) parts.push(`<div class="row"><span class="sw ring" style="border-color:#f4f4f0;border-style:dashed"></span>Detected drip / planting line</div>`);
  document.getElementById("legend").innerHTML = parts.join("") || `<div class="note">No analytical layer active.</div>`;
}

// ---------------------------------------------------------------- tabs
function initTabs() {
  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => openTab(b.dataset.tab)));
}
function openTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  document.querySelectorAll(".tab-body").forEach((s) => { s.hidden = s.dataset.body !== name; });
  hideTip();
}

// ---------------------------------------------------------------- plant detail
function selectPlant(pid) {
  state.selected = pid;
  map.setFilter("selected-ring", ["==", ["get", "plant_id"], pid]);
  map.setFilter("selected-pt", ["==", ["get", "plant_id"], pid]);
  renderPlant(state.byId.get(pid));
  openTab("plant");
}

const colorOf = (classes, key) => classes.find((c) => c.key === key)?.color ?? "#888";

function renderPlant(p) {
  const M = state.summary.models;
  const conf = (v) => `<div class="meter"><span style="width:${(v ?? 0) * 100}%;background:var(--text-2)"></span></div>`;
  document.getElementById("plant-detail").className = "";
  document.getElementById("plant-detail").innerHTML = `
    <div class="plant-title"><h2>Plant ${p.plant_id}</h2><button class="link-btn" id="zoom-plant">Zoom to</button></div>
    <div class="card"><h3><span class="step">1</span>Detection</h3>
      <div class="big">Detected · ${p.position_type === "Planting line" ? "planted position" : "between-line vegetation"}</div>
      <dl class="kv">
        <dt>Detection confidence</dt><dd>${fmt.pct(p.detection_confidence)}</dd>
        <dt>Crown / plant area</dt><dd>${fmt.n(p.area_m2, 3)} m²</dd>
        <dt>Equivalent diameter</dt><dd>${fmt.n(p.equiv_diameter_m)} m</dd>
        <dt>Planting line</dt><dd>${p.line_id ?? "–"} · ${fmt.n(Math.abs(p.dist_to_line_m), 2)} m off</dd>
        <dt>SAM mask quality (IoU)</dt><dd>${fmt.n(p.sam_iou)}</dd>
        <dt>Plant/pit core found</dt><dd>${p.core_found ? "yes" : "no (SAM mask kept)"}</dd>
      </dl>
      ${conf(p.detection_confidence)}
    </div>
    <div class="card"><h3><span class="step">2</span>Identification <span class="badge exp">Experimental</span></h3>
      <div class="big"><span class="dot" style="background:${colorOf(ID_CLASSES, p.plant_class)}"></span>${p.plant_class}</div>
      <dl class="kv">
        <dt>Identification confidence</dt><dd>${fmt.pct(p.identification_confidence)}</dd>
        <dt>P(planted palm)</dt><dd>${fmt.n(p.p_planted_palm)}</dd>
      </dl>
      ${conf(p.identification_confidence)}
      <div class="model-line">${M.identification.name} · v${M.identification.version}</div>
    </div>
    <div class="card"><h3><span class="step">3</span>Health <span class="badge exp">Experimental</span></h3>
      <div class="big"><span class="dot" style="background:${colorOf(HEALTH_CLASSES, p.health_class)}"></span>${p.health_class}</div>
      <dl class="kv">
        <dt>Health score (0–1)</dt><dd>${fmt.n(p.health_score)}</dd>
        <dt>Vigour z (vs field)</dt><dd>${fmt.n(p.health_z)}</dd>
      </dl>
      <h3 style="margin-top:10px">NDVI</h3>
      <dl class="kv">
        <dt>Mean</dt><dd>${fmt.n(p.mean_ndvi, 3)}</dd><dt>Median</dt><dd>${fmt.n(p.median_ndvi, 3)}</dd>
        <dt>P10 / P90</dt><dd>${fmt.n(p.p10_ndvi, 3)} / ${fmt.n(p.p90_ndvi, 3)}</dd>
        <dt>Min / Max</dt><dd>${fmt.n(p.min_ndvi, 3)} / ${fmt.n(p.max_ndvi, 3)}</dd>
        <dt>Std</dt><dd>${fmt.n(p.std_ndvi, 3)}</dd>
        <dt>Soil ring NDVI</dt><dd>${fmt.n(p.bg_ndvi, 3)}</dd>
        <dt>Contrast vs soil</dt><dd>${p.ndvi_contrast >= 0 ? "+" : ""}${fmt.n(p.ndvi_contrast, 3)}</dd>
        <dt>Valid pixels</dt><dd>${fmt.int(p.valid_pixel_count)}</dd>
      </dl>
      <h3 style="margin-top:10px">RGB indicators</h3>
      <dl class="kv">
        <dt>VARI</dt><dd>${fmt.n(p.vari, 3)}</dd><dt>Excess green (ExG)</dt><dd>${fmt.n(p.exg, 3)}</dd>
        <dt>Green fraction</dt><dd>${fmt.pct(p.green_fraction)}</dd><dt>Green area</dt><dd>${fmt.n(p.green_area_m2, 3)} m²</dd>
        <dt>Red / green ratio</dt><dd>${fmt.n(p.rg_ratio, 3)}</dd><dt>Brightness</dt><dd>${fmt.n(p.brightness, 0)}</dd>
      </dl>
      <div class="model-line">${M.health.name} · v${M.health.version}</div>
      <div class="disclaimer">${M.health.disclaimer}</div>
    </div>
    <div class="card"><h3>Model information</h3>
      <dl class="kv small">
        <dt>Detection</dt><dd>v${M.detection.version}</dd>
        <dt>Identification</dt><dd>v${M.identification.version}</dd>
        <dt>Health</dt><dd>v${M.health.version}</dd>
        <dt>Processed</dt><dd>${state.summary.processing_date}</dd>
      </dl>
      <div class="model-line">Detection: ${M.detection.summary}.<br>Identification backbone: ${M.identification.backbone}.</div>
    </div>`;
  document.getElementById("zoom-plant").addEventListener("click", () =>
    map.flyTo({ center: [p.lon, p.lat], zoom: Math.max(map.getZoom(), 22.5) }));
}

// ---------------------------------------------------------------- spatial selection
let drawMode = null;
let draft = [];
function initDraw() {
  const bPoly = document.getElementById("draw-poly");
  const bRect = document.getElementById("draw-rect");
  const bView = document.getElementById("draw-view");
  const bClear = document.getElementById("draw-clear");
  const hint = document.getElementById("draw-hint");

  const start = (mode) => {
    stop();
    drawMode = mode;
    draft = [];
    (mode === "poly" ? bPoly : bRect).classList.add("active");
    map.doubleClickZoom.disable();
    map.getCanvas().style.cursor = "crosshair";
    hint.hidden = false;
    hint.textContent = mode === "poly" ? "Click to add points · double-click (or click the first point) to finish · Esc to cancel"
      : "Click the first corner, then the opposite corner · Esc to cancel";
  };
  const stop = () => {
    drawMode = null;
    draft = [];
    bPoly.classList.remove("active");
    bRect.classList.remove("active");
    hint.hidden = true;
    map.getCanvas().style.cursor = "";
    map.getSource("draft")?.setData(emptyFC());
    setTimeout(() => map.doubleClickZoom.enable(), 300);
  };
  const finish = (ring) => {
    stop();
    if (ring.length < 4) return;
    setSelection(ring);
  };
  const rectRing = (a, b) => [[a[0], a[1]], [b[0], a[1]], [b[0], b[1]], [a[0], b[1]], [a[0], a[1]]];

  bPoly.addEventListener("click", () => (drawMode === "poly" ? stop() : start("poly")));
  bRect.addEventListener("click", () => (drawMode === "rect" ? stop() : start("rect")));
  bView.addEventListener("click", () => {
    stop();
    const b = map.getBounds();
    setSelection(rectRing([b.getWest(), b.getSouth()], [b.getEast(), b.getNorth()]));
  });
  bClear.addEventListener("click", () => {
    state.selection = null;
    map.getSource("selection").setData(emptyFC());
    bClear.disabled = true;
    document.getElementById("selection-detail").className = "empty";
    document.getElementById("selection-detail").innerHTML =
      "Use <b>Polygon</b>, <b>Rectangle</b> or <b>View</b> on the map to select an area. Statistics are computed in your browser for the plants inside it.";
  });
  window.addEventListener("keydown", (e) => { if (e.key === "Escape" && drawMode) stop(); });

  map.on("click", (e) => {
    if (!drawMode) return;
    const p = [e.lngLat.lng, e.lngLat.lat];
    if (drawMode === "rect") {
      draft.push(p);
      if (draft.length === 2) finish(rectRing(draft[0], draft[1]));
      return;
    }
    if (draft.length >= 3) {
      const first = map.project(draft[0]);
      if (Math.hypot(first.x - e.point.x, first.y - e.point.y) < 10) { finish([...draft, draft[0]]); return; }
    }
    draft.push(p);
    drawDraft(p);
  });
  map.on("dblclick", (e) => {
    if (drawMode !== "poly") return;
    e.preventDefault();
    // the two clicks of the double-click already added points; drop the duplicate
    const pts = draft.filter((q, i) => i === 0 || q[0] !== draft[i - 1][0] || q[1] !== draft[i - 1][1]);
    if (pts.length >= 3) finish([...pts, pts[0]]);
  });
  map.on("mousemove", (e) => { if (drawMode && draft.length) drawDraft([e.lngLat.lng, e.lngLat.lat]); });

  function drawDraft(cursor) {
    let coords = [...draft, cursor];
    if (drawMode === "rect" && draft.length === 1) coords = rectRing(draft[0], cursor);
    map.getSource("draft").setData({ type: "FeatureCollection", features: [
      { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: coords } },
      ...draft.map((c) => ({ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: c } })),
    ] });
  }

  function setSelection(ring) {
    state.selection = ring;
    map.getSource("selection").setData({ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [ring] } });
    bClear.disabled = false;
    renderSelection();
    openTab("selection");
  }
}

function pip(pt, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if ((yi > pt[1]) !== (yj > pt[1]) && pt[0] < ((xj - xi) * (pt[1] - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function ringAreaM2(ring) {
  const R = 6378137;
  const lat0 = (ring.reduce((a, c) => a + c[1], 0) / ring.length) * (Math.PI / 180);
  let s = 0;
  for (let i = 0; i < ring.length - 1; i++) {
    const [x1, y1] = ring[i];
    const [x2, y2] = ring[i + 1];
    s += (x1 * Math.cos(lat0)) * y2 - (x2 * Math.cos(lat0)) * y1;
  }
  return Math.abs(s / 2) * (Math.PI / 180) ** 2 * R * R;
}

function renderSelection() {
  const ring = state.selection;
  const xs = ring.map((c) => c[0]);
  const ys = ring.map((c) => c[1]);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const sel = scopedPlants().filter((p) => p.lon >= x0 && p.lon <= x1 && p.lat >= y0 && p.lat <= y1 && pip([p.lon, p.lat], ring));
  const box = document.getElementById("selection-detail");
  box.className = "";
  const areaHa = ringAreaM2(ring) / 1e4;
  if (!sel.length) {
    box.innerHTML = `<div class="sel-head"><span class="n">0</span><span class="l">plants in ${areaHa.toFixed(3)} ha</span></div>
      <div class="empty">No plants in scope inside this area${state.layers.between ? "" : " (between-line vegetation is excluded while its layer is off)"}.</div>`;
    return;
  }
  const mean = (k) => sel.reduce((a, p) => a + (p[k] ?? 0), 0) / sel.length;
  const planted = sel.filter((p) => p.position_type === "Planting line").length;
  box.innerHTML = `
    <div class="sel-head"><span class="n">${fmt.int(sel.length)}</span><span class="l">plants selected · ${areaHa.toFixed(3)} ha</span></div>
    <div class="card"><h3>Summary</h3><dl class="kv">
      <dt>Planted positions</dt><dd>${fmt.int(planted)}</dd>
      <dt>Between-line vegetation</dt><dd>${fmt.int(sel.length - planted)}</dd>
      <dt>Average health score</dt><dd>${fmt.n(mean("health_score"))}</dd>
      <dt>Mean NDVI</dt><dd>${fmt.n(mean("mean_ndvi"), 3)}</dd>
      <dt>Mean identification confidence</dt><dd>${fmt.pct(mean("identification_confidence"))}</dd>
      <dt>Mean detection confidence</dt><dd>${fmt.pct(mean("detection_confidence"))}</dd>
      <dt>Total crown area</dt><dd>${fmt.n(sel.reduce((a, p) => a + p.area_m2, 0), 1)} m²</dd>
    </dl></div>
    <div class="card"><h3>Identification</h3><div id="sel-id"></div></div>
    <div class="card"><h3>Identification confidence</h3><div id="sel-idconf"></div></div>
    <div class="card"><h3>Health</h3><div id="sel-health"></div></div>
    <div class="card"><h3>Mean NDVI per plant</h3><div id="sel-ndvi"></div></div>`;
  const ci = countBy(sel, "plant_class");
  barChart(document.getElementById("sel-id"), ID_CLASSES.map((c) => ({ label: c.key, value: ci[c.key] || 0, color: c.color })));
  histogram(document.getElementById("sel-idconf"), sel.map((p) => p.identification_confidence),
    { min: 0.5, max: 1, bins: 20, color: css("--text-2"), fmt: (v) => `${Math.round(v * 100)}%` });
  const ch = countBy(sel, "health_class");
  barChart(document.getElementById("sel-health"), HEALTH_CLASSES.map((c) => ({ label: c.key, value: ch[c.key] || 0, color: c.color })));
  histogram(document.getElementById("sel-ndvi"), sel.map((p) => p.mean_ndvi), { min: 0, max: 0.6, bins: 24, color: css("--palm"),
    marker: { value: state.summary.stats.field_soil_ndvi_median, label: "soil" } });
}

// ---------------------------------------------------------------- analytics (whole field)
function renderAnalytics() {
  if (!state.plants) return;
  const plants = scopedPlants();
  const box = document.getElementById("analytics");
  const scopeNote = state.layers.between ? "all detections (planted + between-line vegetation)" : "planted positions only · switch on Between-line vegetation to include it";
  box.innerHTML = `
    <p class="muted small" style="margin:0 0 10px">Scope: ${fmt.int(plants.length)} ${scopeNote}.</p>
    <div class="card"><h3>Plant identification</h3><div id="an-id"></div></div>
    <div class="card"><h3>Plant health · relative vigour</h3><div id="an-health"></div>
      <div class="chart-cap">Classes are robust-z bands relative to this field, not absolute thresholds.</div></div>
    <div class="card"><h3>Mean NDVI per plant</h3><div id="an-ndvi"></div>
      <div class="chart-cap">Dashed line: median NDVI of the bare soil around plants.</div></div>
    <div class="card"><h3>Identification confidence</h3><div id="an-conf"></div>
      <div class="chart-cap">Below ${Math.round(state.summary.models.identification.min_confidence * 100)}% a plant is reported as Unclassified.</div></div>`;
  const ci = countBy(plants, "plant_class");
  barChart(document.getElementById("an-id"), ID_CLASSES.map((c) => ({ label: c.key, value: ci[c.key] || 0, color: c.color })));
  const ch = countBy(plants, "health_class");
  barChart(document.getElementById("an-health"), HEALTH_CLASSES.map((c) => ({ label: c.key, value: ch[c.key] || 0, color: c.color })));
  histogram(document.getElementById("an-ndvi"), plants.map((p) => p.mean_ndvi), { min: 0, max: 0.6, bins: 30, color: css("--palm"),
    marker: { value: state.summary.stats.field_soil_ndvi_median, label: "soil" } });
  histogram(document.getElementById("an-conf"), plants.map((p) => p.identification_confidence),
    { min: 0.5, max: 1, bins: 25, color: css("--text-2"), fmt: (v) => `${Math.round(v * 100)}%` });
}

// ---------------------------------------------------------------- models / provenance
function renderModels(s) {
  const M = s.models;
  const trial = M.identification.trial;
  const rows = Object.entries(trial).filter(([, v]) => v && typeof v === "object" && "roc_auc" in v)
    .sort((a, b) => b[1].roc_auc - a[1].roc_auc);
  const best = `${M.identification.backbone.includes("dinov3") ? "dinov3-vitl16-sat493m" : "dinov2-base"}_crown+tabular`;
  document.getElementById("models").innerHTML = `
    <p class="muted small" style="margin-top:0">Three separate stages. Each result on the map carries the model and version that produced it.</p>
    <div class="card"><h3><span class="step">1</span>Plant detection</h3>
      <div>${M.detection.name}</div>
      <div class="model-line">v${M.detection.version}. ${M.detection.summary}. Confidence is a ${M.detection.confidence_note}.</div>
      <div class="model-line"><b>Tested and rejected:</b><br>${Object.entries(M.detection.rejected).map(([k, v]) => `${k}: ${v}`).join("<br>")}</div>
    </div>
    <div class="card"><h3><span class="step">2</span>Plant identification <span class="badge exp">Experimental</span></h3>
      <div>${M.identification.name}</div>
      <div class="model-line">Backbone ${M.identification.backbone} · v${M.identification.version}. Classes: ${M.identification.classes.join(", ")}.
        Labels are weak: planted = on a detected drip line with a clear core (${fmt.int(M.identification.weak_labels.planted)}), other = vegetation between lines (${fmt.int(M.identification.weak_labels.other)}). Position is not a model input; the drip line is removed from crops.
        Species "palm" is the declared planting species and cannot be confirmed from imagery at seedling size.</div>
      <div class="model-line">Spatial 5-fold CV (25 m blocks): ROC-AUC <b>${fmt.n(M.identification.spatial_cv.roc_auc, 3)}</b>, balanced accuracy <b>${fmt.n(M.identification.spatial_cv.balanced_accuracy, 3)}</b>.</div>
      <table class="mini"><thead><tr><th>Candidate inputs</th><th class="num">AUC</th><th class="num">Bal. acc.</th></tr></thead><tbody>
        ${rows.map(([k, v]) => `<tr class="${k === best ? "best" : ""}"><td>${k.replaceAll("_", " ")}</td><td class="num">${fmt.n(v.roc_auc, 3)}</td><td class="num">${fmt.n(v.balanced_accuracy, 3)}</td></tr>`).join("")}
      </tbody></table>
    </div>
    <div class="card"><h3><span class="step">3</span>Plant health <span class="badge exp">Experimental</span></h3>
      <div>${M.health.name}</div>
      <div class="model-line">v${M.health.version} · ${M.health.approach}.<br>Weights: ${Object.entries(M.health.weights).map(([k, v]) => `${k} ${v}`).join(", ")}.<br>
        Internal consistency: NDVI-only vs RGB-only sub-scores Spearman ρ = ${fmt.n(M.health.consistency_spearman_ndvi_vs_rgb, 2)}.</div>
      <div class="disclaimer">${M.health.disclaimer}</div>
    </div>
    <div class="card"><h3>Source data</h3><dl class="kv small">
      <dt>RGB</dt><dd>${s.data.rgb.file} · ${(s.data.rgb.pixel_size_m * 1000).toFixed(0)} mm</dd>
      <dt>NDVI</dt><dd>${s.data.ndvi.file} · ${(s.data.ndvi.pixel_size_m * 100).toFixed(2)} cm</dd>
      <dt>CRS</dt><dd>${s.data.crs}</dd>
      <dt>NDVI range</dt><dd>${fmt.n(s.data.ndvi.range[0])} … ${fmt.n(s.data.ndvi.range[1])}</dd>
      <dt>Processed</dt><dd>${s.processing_date}</dd></dl></div>`;
}

boot().catch((e) => {
  console.error(e);
  document.getElementById("loading").textContent = `Failed to load data: ${e.message}`;
});
