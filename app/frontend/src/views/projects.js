// Project picker: project locations on a map. Clicking a location flies to the site, shows its
// orthomosaic and headline results, and opens the project workspace.
import * as maplibregl from "maplibre-gl";
import { BUNDLED_SURVEYS, peekSurvey, useSurvey } from "../store.js";
import { tileUrl } from "../tiles.js";
import { esc, fmt, navigate } from "../ui.js";

const PROJECTS = [
  { id: "pilot", survey: BUNDLED_SURVEYS[0], name: "Pilot", place: "Kuwait", kind: "Revegetation block" },
];
const BASEMAP = "https://tiles.openfreemap.org/styles/positron";   // free, no key, OpenStreetMap data
const HEALTH = ["#12805a", "#6cc79f", "#9a9994", "#ef9a8a", "#c93a3a"];

async function basemap() {
  try {
    const r = await fetch(BASEMAP, { signal: AbortSignal.timeout(5000) });
    if (r.ok) return await r.json();
  } catch { /* offline: plain background */ }
  return { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": "#eceae1" } }] };
}

function center(b) { return [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]; }

function outputs(s) {
  const st = s.summary.stats;
  const hc = st.health_counts_planted || {};
  const classes = s.summary.models.health.classes || [];
  const total = classes.reduce((a, k) => a + (hc[k] || 0), 0) || 1;
  const species = s.summary.declared_species.split(" ")[0];
  const cells = [
    ["Planting spots", st.expected_planting_positions ?? st.planted_positions, "expected from the drip lines"],
    ["Plants located", st.planted_positions, `${fmt.pct(st.planted_positions / (st.expected_planting_positions || st.planted_positions), 1)} of spots`],
    ["Green canopy", st.green_canopy_planted, `${fmt.pct(st.green_canopy_planted / st.planted_positions, 1)} of plants`],
    [`Identified as ${species}`, st.planted_identified_as_species, `${fmt.pct(st.planted_identified_as_species / st.planted_positions, 1)} of plants`],
  ];
  return `
    <div class="p-out">
      <div class="p-nums">${cells.map(([k, v, d]) => `<div><span class="k">${esc(k)}</span><span class="v num">${fmt.int(v)}</span><span class="d">${esc(d)}</span></div>`).join("")}</div>
      <div class="p-health"><span class="k">Plant health</span>
        <div class="health-strip">${classes.map((k, i) => `<span title="${esc(k)}: ${fmt.int(hc[k] || 0)}" style="width:${((hc[k] || 0) / total) * 100}%;background:${HEALTH[i] || "#999"}"></span>`).join("")}</div>
        <div class="legend-row small">${classes.map((k, i) => `<span><span class="dot" style="background:${HEALTH[i] || "#999"}"></span>${esc(k.replace(" condition", ""))} <span class="n">${fmt.int(hc[k] || 0)}</span></span>`).join("")}</div>
      </div>
      <p class="small p-attn"><b class="num">${fmt.int((st.no_green_canopy_planted || 0) + (st.inferred_missing_positions || 0))}</b> spots need a field check: ${fmt.int(st.no_green_canopy_planted)} plants without green canopy and ${fmt.int(st.inferred_missing_positions)} empty spots.</p>
      <div class="p-actions">
        <button class="btn primary" data-open="#/overview">Open results</button>
        <button class="btn" data-open="#/map">Open map</button>
        <button class="btn" data-open="#/plants">Plants</button>
      </div>
    </div>`;
}

export function renderProjects(el) {
  el.innerHTML = `
  <div class="projects">
    <aside class="p-side">
      <div class="p-head"><div class="eyebrow">Projects</div><h1>Pick a project</h1>
        <p class="muted">Click a project location on the map to see its results; every planted sapling appears, coloured by condition.</p></div>
      <div id="p-list" class="p-list"><p class="muted">Loading projects…</p></div>
      <a class="p-new" href="#/analyze"><b>+ New survey</b><span>Upload RGB + NDVI and run the models on a compute node</span></a>
    </aside>
    <div class="p-map"><div id="pmap"></div><div class="p-hint" id="p-hint">Click the pin to open the project</div></div>
  </div>`;

  let map = null;
  let gone = false;
  const markers = new Map();
  const list = el.querySelector("#p-list");
  const loaded = new Map();

  async function select(id, fly = true) {
    const pr = PROJECTS.find((p) => p.id === id);
    const s = loaded.get(id);
    if (!pr || !s) return;
    list.querySelectorAll(".p-card").forEach((c) => {
      const on = c.dataset.id === id;
      c.classList.toggle("on", on);
      c.setAttribute("aria-expanded", String(on));
      c.querySelector(".p-body").innerHTML = on ? outputs(s) : "";
    });
    markers.forEach((m, k) => m.getElement().classList.toggle("on", k === id));
    el.querySelector("#p-hint").hidden = true;
    list.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", async (e) => {
      e.stopPropagation();
      b.textContent = "Opening…";
      await useSurvey(pr.survey);
      navigate(b.dataset.open);
    }));
    if (fly && map) {
      const b = s.tiles.layers.rgb.bounds;
      map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 60, duration: 2600, essential: true });
    }
    // load the full survey now (the workspace needs it next) and show its plants by condition
    useSurvey(pr.survey).then((full) => whenReady(() => showPlants(pr.id, full))).catch(() => {});
  }

  let ready = false;
  const queue = [];
  function whenReady(fn) { if (ready) fn(); else queue.push(fn); }
  function showPlants(id, full) {
    if (gone || !map || map.getSource(`plants-${id}`)) return;
    const color = new Map(full.healthClasses.map((c) => [c.key, c.color]));
    const features = full.list.filter((p) => p.position_type === "Planting line").map((p) => ({
      type: "Feature", geometry: { type: "Point", coordinates: [p.lon, p.lat] },
      properties: { c: color.get(p.health_class) || "#9a9994" },
    }));
    map.addSource(`plants-${id}`, { type: "geojson", data: { type: "FeatureCollection", features } });
    map.addLayer({
      id: `plants-${id}`, type: "circle", source: `plants-${id}`, minzoom: 14,
      paint: {
        "circle-color": ["get", "c"], "circle-stroke-color": "#ffffff", "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 18, 0, 19.5, 1],
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 15, 1, 17, 1.7, 18, 2.6, 19, 4, 21, 10],
        "circle-opacity": ["interpolate", ["linear"], ["zoom"], 14, 0, 15, 0.95],
      },
    });
  }

  Promise.all(PROJECTS.map((p) => peekSurvey(p.survey).then((s) => loaded.set(p.id, s)))).then(async () => {
    if (gone) return;
    list.innerHTML = PROJECTS.map((p) => {
      const s = loaded.get(p.id);
      const st = s.summary.stats;
      const c = center(s.tiles.layers.rgb.bounds);
      return `<div class="p-card" data-id="${esc(p.id)}" role="button" tabindex="0" aria-expanded="false">
        <div class="p-top"><div><b class="p-name">${esc(p.name)}</b><span class="chip ok">Processed</span></div>
          <span class="muted small">${esc(p.kind)} · ${esc(s.summary.declared_species)}</span>
          <span class="muted small num">${esc(p.place)} · ${fmt.n(c[1], 3)}° N, ${fmt.n(c[0], 3)}° E · ${fmt.n(st.surveyed_area_ha, 2)} ha · processed ${esc(s.summary.processing_date)}</span></div>
        <div class="p-body"></div>
      </div>`;
    }).join("");
    list.querySelectorAll(".p-card").forEach((c) => {
      c.addEventListener("click", () => select(c.dataset.id));
      c.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(c.dataset.id); } });
    });

    const style = await basemap();
    if (gone) return;
    const first = loaded.get(PROJECTS[0].id);
    map = new maplibregl.Map({
      container: "pmap", style, center: center(first.tiles.layers.rgb.bounds), zoom: 5.2, minZoom: 2, maxZoom: 21,
      attributionControl: { compact: true },
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.on("zoom", () => markers.forEach((m) => m.getElement().classList.toggle("near", map.getZoom() > 14.5)));   // the imagery takes over
    for (const p of PROJECTS) {
      const s = loaded.get(p.id);
      const pin = document.createElement("button");
      pin.className = "pin";
      pin.setAttribute("aria-label", `${p.name} project location`);
      pin.innerHTML = `<span class="pin-dot"></span><span class="pin-label">${esc(p.name)}<small>${fmt.int(s.summary.stats.planted_positions)} plants</small></span>`;
      pin.addEventListener("click", (e) => { e.stopPropagation(); select(p.id); });
      markers.set(p.id, new maplibregl.Marker({ element: pin, anchor: "center" }).setLngLat(center(s.tiles.layers.rgb.bounds)).addTo(map));
    }
    map.on("load", () => {
      for (const p of PROJECTS) {   // the site's own orthomosaic and outline when zoomed in
        const s = loaded.get(p.id);
        const L = s.tiles.layers.rgb;
        const [x0, y0, x1, y1] = L.bounds;
        map.addSource(`img-${p.id}`, { type: "raster", tiles: [tileUrl(s.id, "rgb")], tileSize: 256, bounds: L.bounds, minzoom: L.minzoom, maxzoom: L.maxzoom });
        map.addLayer({ id: `img-${p.id}`, type: "raster", source: `img-${p.id}`, minzoom: 15 });
        map.addSource(`area-${p.id}`, { type: "geojson", data: { type: "Feature", geometry: { type: "Polygon", coordinates: [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]] } } });
        map.addLayer({ id: `area-${p.id}`, type: "line", source: `area-${p.id}`, minzoom: 12, paint: { "line-color": "#2f8a3a", "line-width": 2, "line-dasharray": [2, 1] } });
      }
      ready = true;
      queue.splice(0).forEach((fn) => fn());
    });
  }).catch((e) => { list.innerHTML = `<p class="note">Could not load projects: ${esc(e.message)}</p>`; });

  return () => { gone = true; map?.remove(); };
}
