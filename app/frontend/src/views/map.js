// Map screen: one persistent MapLibre map; rebuilt only when the survey changes.
import * as maplibregl from "maplibre-gl";
import { tileUrl } from "../tiles.js";
import { colorOf, countBy, esc, fmt, kv, mean, pip, ringAreaM2 } from "../ui.js";
import { thumbStyle } from "../store.js";

const POLY_MINZOOM = 19.2;
let map = null;
let current = null; // survey shown on the map
let colorBy = "detection";
const layers = { rgb: true, ndvi: false, vegmask: false, lines: false, gaps: true, between: false };
let selection = null;
let drawMode = null;
let draft = [];
let pendingPlant = null;

const $ = (id) => document.getElementById(id);
const emptyFC = () => ({ type: "FeatureCollection", features: [] });

export function hideMap() { /* the map stays alive; nothing to tear down */ }

export function showMap(s, params) {
  pendingPlant = params.get("plant");
  if (!map) createMap(s);
  else if (current !== s) rebuild(s);
  else {
    map.resize();
    focusPendingPlant();
  }
  renderLayerPanel();
  if (!pendingPlant && !selection) renderDetailIntro();
}

function createMap(s) {
  current = s;
  map = new maplibregl.Map({
    container: "map", style: buildStyle(s), bounds: boundsOf(s), fitBoundsOptions: { padding: 30 },
    minZoom: 15, maxZoom: 24.5, attributionControl: { compact: true, customAttribution: `<a href="${new URL("third-party-licenses.txt", document.baseURI).href}" target="_blank" rel="noopener">Credits</a>` },
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: true }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");
  map.on("load", () => { addLayers(); applyVisibility(); $("map-loading").hidden = true; focusPendingPlant(); });
  map.on("error", (e) => console.warn(e.error?.message || e));
  bindInteractions();
  initDraw();
}

function rebuild(s) {
  current = s;
  selection = null;
  $("map-loading").hidden = false;
  map.setStyle(buildStyle(s));
  map.once("style.load", () => {
    addLayers();
    applyVisibility();
    map.fitBounds(boundsOf(s), { padding: 30, duration: 0 });
    $("map-loading").hidden = true;
    focusPendingPlant();
  });
}

function boundsOf(s) {
  const b = s.tiles.layers.rgb.bounds;
  return [[b[0], b[1]], [b[2], b[3]]];
}

function buildStyle(s) {
  const raster = (name) => ({
    type: "raster", tiles: [tileUrl(s.id, name)], tileSize: 256, bounds: s.tiles.layers[name].bounds,
    minzoom: s.tiles.layers[name].minzoom, maxzoom: s.tiles.layers[name].maxzoom,
  });
  const points = {
    type: "FeatureCollection",
    features: s.list.map((p) => ({ type: "Feature", properties: p, geometry: { type: "Point", coordinates: [p.lon, p.lat] } })),
  };
  return {
    version: 8,
    sources: {
      rgb: { ...raster("rgb"), attribution: `${s.summary.brand} · ${s.summary.project} drone survey` },
      ndvi: raster("ndvi"), vegmask: raster("vegmask"),
      plants: { type: "geojson", data: s.plants },
      points: { type: "geojson", data: points },
      lines: { type: "geojson", data: s.lines },
      gaps: { type: "geojson", data: s.gaps },
      selection: { type: "geojson", data: emptyFC() },
      draft: { type: "geojson", data: emptyFC() },
    },
    layers: [{ id: "bg", type: "background", paint: { "background-color": "#e8e6de" } }],
  };
}

const match = (field, classes, fallback = "#888") => ["match", ["get", field], ...classes.flatMap((c) => [c.key, c.color]), fallback];
const PLANTED = ["==", ["get", "position_type"], "Planting line"];
const NOT_PLANTED = ["!=", ["get", "position_type"], "Planting line"];

function fillColor() {
  const s = current;
  if (colorBy === "canopy") return ["case", ["==", ["get", "living_canopy"], true], "#8fd46b", "#e06a5a"];
  if (colorBy === "ident") return match("plant_class", s.idClasses);
  if (colorBy === "health") return match("health_class", s.healthClasses, "#555");
  return "#ffd166";
}

function addLayers() {
  map.addLayer({ id: "rgb", type: "raster", source: "rgb", paint: { "raster-fade-duration": 150 } });
  map.addLayer({ id: "ndvi", type: "raster", source: "ndvi", paint: { "raster-resampling": "nearest" } });
  map.addLayer({ id: "vegmask", type: "raster", source: "vegmask", paint: { "raster-opacity": 0.9 } });
  map.addLayer({ id: "lines", type: "line", source: "lines",
    paint: { "line-color": "#eef1ea", "line-opacity": 0.5, "line-width": ["interpolate", ["linear"], ["zoom"], 17, 0.5, 22, 1.5], "line-dasharray": [3, 2] } });
  const radius = ["interpolate", ["exponential", 2], ["zoom"], 15, 0.4, 17, 0.8, 18, 1.8, 19.2, 4.5];
  map.addLayer({ id: "between-fill", type: "line", source: "plants", minzoom: POLY_MINZOOM, filter: NOT_PLANTED,
    paint: { "line-color": "#5fd4e8", "line-width": 1.2, "line-dasharray": [2, 1] } });
  map.addLayer({ id: "between-pt", type: "circle", source: "points", maxzoom: POLY_MINZOOM, filter: NOT_PLANTED,
    paint: { "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": "#5fd4e8", "circle-stroke-width": 1, "circle-radius": radius } });
  map.addLayer({ id: "plant-fill", type: "fill", source: "plants", minzoom: POLY_MINZOOM, filter: PLANTED,
    paint: { "fill-color": fillColor(), "fill-opacity": 0.55 } });
  map.addLayer({ id: "plant-line", type: "line", source: "plants", minzoom: POLY_MINZOOM, filter: PLANTED,
    paint: { "line-color": fillColor(), "line-width": ["interpolate", ["linear"], ["zoom"], 19, 1, 23, 2.2] } });
  map.addLayer({ id: "plant-pt", type: "circle", source: "points", maxzoom: POLY_MINZOOM, filter: PLANTED,
    paint: { "circle-color": fillColor(), "circle-radius": radius, "circle-opacity": 0.95 } });
  map.addLayer({ id: "gaps", type: "circle", source: "gaps",
    paint: { "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": "#ff6f91", "circle-stroke-width": 2,
      "circle-radius": ["interpolate", ["exponential", 2], ["zoom"], 15, 1.5, 19, 5, 23, 14] } });
  map.addLayer({ id: "selection-fill", type: "fill", source: "selection", paint: { "fill-color": "#8fd46b", "fill-opacity": 0.08 } });
  map.addLayer({ id: "selection-line", type: "line", source: "selection", paint: { "line-color": "#8fd46b", "line-width": 2 } });
  map.addLayer({ id: "draft-line", type: "line", source: "draft", paint: { "line-color": "#8fd46b", "line-width": 2, "line-dasharray": [2, 1] } });
  map.addLayer({ id: "draft-pt", type: "circle", source: "draft", filter: ["==", ["geometry-type"], "Point"],
    paint: { "circle-color": "#8fd46b", "circle-radius": 4 } });
  map.addLayer({ id: "sel-ring", type: "line", source: "plants", filter: ["==", ["get", "plant_id"], ""], paint: { "line-color": "#fff", "line-width": 3 } });
  map.addLayer({ id: "sel-pt", type: "circle", source: "points", maxzoom: POLY_MINZOOM, filter: ["==", ["get", "plant_id"], ""],
    paint: { "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": "#fff", "circle-stroke-width": 2.5, "circle-radius": 9 } });
}

function applyVisibility() {
  if (!map?.getLayer("rgb")) return;
  const vis = (ids, on) => ids.forEach((id) => map.getLayer(id) && map.setLayoutProperty(id, "visibility", on ? "visible" : "none"));
  vis(["rgb"], layers.rgb);
  vis(["ndvi"], layers.ndvi);
  vis(["vegmask"], layers.vegmask);
  vis(["lines"], layers.lines);
  vis(["gaps"], layers.gaps);
  vis(["between-fill", "between-pt"], layers.between);
  const c = fillColor();
  map.setPaintProperty("plant-fill", "fill-color", c);
  map.setPaintProperty("plant-fill", "fill-opacity", colorBy === "detection" ? 0 : 0.6);
  map.setPaintProperty("plant-line", "line-color", c);
  map.setPaintProperty("plant-pt", "circle-color", c);
}

// ------------------------------------------------------------------ layer panel + legend
function renderLayerPanel() {
  const s = current;
  const st = s.summary.stats;
  const opt = (k, label, sub) => `<label class="layer"><input type="radio" name="colorby" value="${k}" ${colorBy === k ? "checked" : ""}/><span>${label}<small>${sub}</small></span></label>`;
  const chk = (k, label, sub) => `<label class="layer"><input type="checkbox" data-layer="${k}" ${layers[k] ? "checked" : ""}/><span>${label}<small>${sub}</small></span></label>`;
  $("layer-panel").innerHTML = `
    <div class="layer-group"><div class="group-label">Colour plants by</div>
      ${opt("detection", "Detection", `${fmt.int(st.planted_positions)} plants located`)}
      ${opt("canopy", "Canopy", "green vs no green canopy")}
      ${opt("ident", "Identification <span class='badge'>Exp.</span>", "what plant it is")}
      ${opt("health", "Health <span class='badge'>Exp.</span>", `${s.healthClasses.length} condition groups`)}
    </div>
    <div class="layer-group"><div class="group-label">Show</div>
      ${chk("gaps", "Empty planting spots", `${fmt.int(st.inferred_missing_positions)} expected, no plant`)}
      ${chk("between", "Between-line vegetation", "weeds / annuals")}
      ${chk("lines", "Drip lines", `${st.drip_lines ?? st.planting_lines} detected, ${st.planting_lines} planted`)}
    </div>
    <div class="layer-group"><div class="group-label">Imagery</div>
      ${chk("rgb", "RGB orthomosaic", `${fmt.n(s.summary.data.rgb.pixel_size_m * 1000, 0)} mm`)}
      ${chk("ndvi", "NDVI", `${fmt.n(s.summary.data.ndvi.pixel_size_m * 100, 1)} cm`)}
      ${chk("vegmask", "Vegetation mask", "NDVI above local soil")}
    </div>
    <div class="legend" id="legend"></div>`;
  $("layer-panel").querySelectorAll("input[name=colorby]").forEach((r) => r.addEventListener("change", () => {
    colorBy = r.value;
    applyVisibility();
    renderLegend();
  }));
  $("layer-panel").querySelectorAll("input[data-layer]").forEach((c) => c.addEventListener("change", () => {
    layers[c.dataset.layer] = c.checked;
    applyVisibility();
    renderLegend();
  }));
  renderLegend();
}

function renderLegend() {
  const s = current;
  const planted = s.list.filter((p) => p.position_type === "Planting line");
  const row = (color, label, n, ring) => `<div class="row"><span class="sw ${ring ? "ring" : ""}" style="${ring ? `border-color:${color}` : `background:${color}`}"></span>${esc(label)}<span class="count">${n == null ? "" : fmt.int(n)}</span></div>`;
  let body = "";
  if (colorBy === "detection") body = `<h4>Detected plants</h4>${row("#ffd166", "Planted sapling", planted.length, true)}`;
  if (colorBy === "canopy") {
    const g = planted.filter((p) => p.living_canopy).length;
    body = `<h4>Canopy</h4>${row("#8fd46b", "Green canopy", g)}${row("#e06a5a", "No green canopy", planted.length - g)}`;
  }
  if (colorBy === "ident") {
    const c = countBy(planted, "plant_class");
    body = `<h4>Identification</h4>${s.idClasses.map((x) => row(x.color, x.key, c[x.key] || 0)).join("")}`;
  }
  if (colorBy === "health") {
    const c = countBy(planted, "health_class");
    body = `<h4>Health</h4>${s.healthClasses.map((x) => row(x.color, x.key, c[x.key] || 0)).join("")}`;
  }
  if (layers.gaps) body += row("#ff6f91", "Empty planting spot", s.gaps.features.length, true);
  if (layers.between) body += row("#5fd4e8", "Between-line vegetation", s.list.length - planted.length, true);
  if (layers.ndvi) body += `<div><h4>NDVI</h4><div class="ramp" style="background:linear-gradient(90deg,#a50026,#f46d43,#fee08b,#d9ef8b,#66bd63,#006837)"></div>
    <div class="ramp-labels"><span>≤ -0.05</span><span>0.25</span><span>≥ 0.55</span></div></div>`;
  $("legend").innerHTML = body + `<p class="small muted" style="margin:6px 0 0">Outlines from zoom 19; dots below.</p>`;
}

// ------------------------------------------------------------------ details panel
function renderDetailIntro() {
  const s = current;
  const st = s.summary.stats;
  $("map-detail").innerHTML = `
    <div class="detail-title"><h2>${esc(s.summary.project)}</h2></div>
    <p class="muted" style="margin-top:0">Click a plant to inspect it, or select an area with <b>Polygon</b>, <b>Rectangle</b> or <b>Whole view</b>.</p>
    ${summaryCard(s.list.filter((p) => p.position_type === "Planting line"), "Whole survey")}
    <p class="small muted">${fmt.int(st.inferred_missing_positions)} empty planting spots (pink rings) · ${fmt.int(st.between_line_vegetation)} between-line objects.</p>`;
}

function summaryCard(plants, title) {
  const s = current;
  const n = plants.length;
  const green = plants.filter((p) => p.living_canopy).length;
  const ident = plants.filter((p) => p.plant_class === s.summary.stats.planted_class).length;
  const hc = countBy(plants, "health_class");
  return `<div class="card">
    <h3>${esc(title)}</h3>
    ${kv([["Planted saplings", fmt.int(n)], ["Green canopy", `${fmt.int(green)} · ${fmt.pct(green / (n || 1))}`],
      ["Planted stock", `${fmt.int(ident)} · ${fmt.pct(ident / (n || 1))}`],
      ["Mean NDVI", fmt.n(mean(plants, "mean_ndvi"))]])}
    <div class="health-strip" style="margin-top:12px">${s.healthClasses.map((c) => `<span title="${esc(c.key)}: ${hc[c.key] || 0}" style="width:${((hc[c.key] || 0) / (n || 1)) * 100}%;background:${c.color}"></span>`).join("")}</div>
    <div class="legend-row" style="font-size:12px">${s.healthClasses.map((c) => `<span><span class="dot" style="background:${c.color}"></span> ${fmt.int(hc[c.key] || 0)}</span>`).join("")}</div>
  </div>`;
}

function renderPlantCard(p) {
  const s = current;
  $("map-detail").innerHTML = `
    <div class="detail-title"><h2 class="num">${esc(p.plant_id)}</h2><a class="btn" href="#/plants/${p.plant_id}">Open plant page →</a></div>
    <div style="display:flex;gap:12px;align-items:center;margin-bottom:12px">
      <span class="thumb" style="${thumbStyle(s, p.plant_id, "rgb", 96)}"></span>
      <span class="thumb" style="${thumbStyle(s, p.plant_id, "ndvi", 96)}"></span>
    </div>
    <div class="card"><div class="stack">
      <div class="status-line" style="font-size:18px">${p.position_type === "Planting line" ? (p.living_canopy ? "Living sapling" : "No green canopy") : "Between-line vegetation"}</div>
      ${kv([["Identification", `${esc(p.plant_class)} · ${fmt.pct(p.identification_confidence)}`],
        ["Health", p.health_class ? `${esc(p.health_class)} · ${fmt.n(p.health_score)}` : "not graded"],
        ["Green canopy", `${fmt.int(p.canopy_area_m2 * 1e4)} cm²`], ["Mean NDVI", fmt.n(p.mean_ndvi)],
        ["Detection confidence", fmt.pct(p.detection_confidence)], ["Drip line", esc(p.line_id ?? "–")]])}
    </div></div>
    <p><button class="btn" id="back-intro">← Survey summary</button></p>`;
  $("back-intro").addEventListener("click", () => { selectPlant(null); renderDetailIntro(); });
}

function selectPlant(id) {
  map.setFilter("sel-ring", ["==", ["get", "plant_id"], id || ""]);
  map.setFilter("sel-pt", ["==", ["get", "plant_id"], id || ""]);
}

function focusPendingPlant() {
  if (!pendingPlant || !map?.getLayer("sel-ring")) return;
  const p = current.byId.get(pendingPlant);
  pendingPlant = null;
  if (!p) return;
  selectPlant(p.plant_id);
  renderPlantCard(p);
  map.jumpTo({ center: [p.lon, p.lat], zoom: 22.3 });
}

function bindInteractions() {
  const clickable = ["plant-fill", "plant-line", "plant-pt", "between-fill", "between-pt"];
  const visible = () => clickable.filter((l) => map.getLayer(l) && map.getLayoutProperty(l, "visibility") !== "none");
  map.on("click", (e) => {
    if (drawMode) return;
    const f = map.queryRenderedFeatures([[e.point.x - 5, e.point.y - 5], [e.point.x + 5, e.point.y + 5]], { layers: visible() });
    let id = f[0]?.properties?.plant_id;
    if (!id && map.getZoom() >= POLY_MINZOOM) {
      const pt = [e.lngLat.lng, e.lngLat.lat];
      const hit = current.plants.features.find((ft) => Math.abs(ft.properties.lon - pt[0]) < 2e-5 && Math.abs(ft.properties.lat - pt[1]) < 2e-5 && pip(pt, ft.geometry.coordinates[0]));
      id = hit?.properties.plant_id;
    }
    if (id) {
      selectPlant(id);
      renderPlantCard(current.byId.get(id));
    }
  });
  map.on("mousemove", (e) => {
    if (drawMode) return;
    map.getCanvas().style.cursor = map.queryRenderedFeatures(e.point, { layers: visible() }).length ? "pointer" : "";
  });
}

// ------------------------------------------------------------------ area selection
function initDraw() {
  const bPoly = $("draw-poly");
  const bRect = $("draw-rect");
  const hint = $("draw-hint");
  const rectRing = (a, b) => [[a[0], a[1]], [b[0], a[1]], [b[0], b[1]], [a[0], b[1]], [a[0], a[1]]];
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
  const start = (mode) => {
    stop();
    drawMode = mode;
    (mode === "poly" ? bPoly : bRect).classList.add("active");
    map.doubleClickZoom.disable();
    map.getCanvas().style.cursor = "crosshair";
    hint.hidden = false;
    hint.textContent = mode === "poly" ? "Click to add corners · double-click to finish · Esc to cancel" : "Click two opposite corners · Esc to cancel";
  };
  const finish = (ring) => { stop(); if (ring.length >= 4) setSelection(ring); };
  bPoly.onclick = () => (drawMode === "poly" ? stop() : start("poly"));
  bRect.onclick = () => (drawMode === "rect" ? stop() : start("rect"));
  $("draw-view").onclick = () => {
    stop();
    const b = map.getBounds();
    setSelection(rectRing([b.getWest(), b.getSouth()], [b.getEast(), b.getNorth()]));
  };
  $("draw-clear").onclick = () => {
    selection = null;
    map.getSource("selection").setData(emptyFC());
    $("draw-clear").disabled = true;
    renderDetailIntro();
  };
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
      const f = map.project(draft[0]);
      if (Math.hypot(f.x - e.point.x, f.y - e.point.y) < 10) { finish([...draft, draft[0]]); return; }
    }
    draft.push(p);
    drawDraft(p);
  });
  map.on("dblclick", (e) => {
    if (drawMode !== "poly") return;
    e.preventDefault();
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
}

function setSelection(ring) {
  selection = ring;
  map.getSource("selection").setData({ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [ring] } });
  $("draw-clear").disabled = false;
  const xs = ring.map((c) => c[0]);
  const ys = ring.map((c) => c[1]);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const inside = current.list.filter((p) => p.position_type === "Planting line" && p.lon >= x0 && p.lon <= x1 && p.lat >= y0 && p.lat <= y1 && pip([p.lon, p.lat], ring));
  const gaps = current.gaps.features.filter((g) => pip(g.geometry.coordinates, ring)).length;
  $("map-detail").innerHTML = `
    <div class="detail-title"><h2>Selected area</h2><span class="muted num">${fmt.n(ringAreaM2(ring) / 1e4, 3)} ha</span></div>
    ${inside.length ? summaryCard(inside, `${fmt.int(inside.length)} planted saplings`) : '<p class="empty">No planted saplings inside this area.</p>'}
    <p class="small muted">${fmt.int(gaps)} empty planting spots inside.</p>
    ${inside.length ? `<a class="btn" href="#/plants?line=${esc(mostCommon(inside, "line_id"))}">Open the plant list →</a>` : ""}`;
}

function mostCommon(list, key) {
  const c = countBy(list, key);
  return Object.entries(c).sort((a, b) => b[1] - a[1])[0]?.[0] || "";
}

export { colorOf };
