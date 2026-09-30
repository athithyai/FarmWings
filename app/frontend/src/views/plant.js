import { thumbStyle } from "../store.js";
import { colorOf, esc, fmt, kv, meter, toLocal } from "../ui.js";

export function renderPlant(el, s, id) {
  const p = s.byId.get(id);
  if (!p) {
    el.innerHTML = `<div class="card"><h3>Plant ${esc(id)} not found</h3><p class="muted">It is not in the survey “${esc(s.summary.project)}”.</p><a class="btn" href="#/plants">Back to plants</a></div>`;
    return;
  }
  const M = s.summary.models;
  const idx = s.list.findIndex((q) => q.plant_id === id);
  const prev = s.list[idx - 1];
  const next = s.list[idx + 1];
  const planted = p.position_type === "Planting line";
  const onLine = s.list.filter((q) => q.line_id === p.line_id && q.position_type === "Planting line");
  const k = onLine.findIndex((q) => q.plant_id === id);
  const neighbours = k >= 0 ? onLine.slice(Math.max(0, k - 4), k + 5) : [];
  const hColor = colorOf(s.healthClasses, p.health_class);
  const green = p.living_canopy;

  el.innerHTML = `
    <div class="crumbs"><a href="#/plants">Plants</a> / ${planted ? `line <a href="#/plants?line=${p.line_id}">${esc(p.line_id)}</a>` : "between-line vegetation"}</div>
    <div class="plant-head">
      <h1 class="num">${esc(p.plant_id)}</h1>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        ${prev ? `<a class="btn" href="#/plants/${prev.plant_id}">← ${prev.plant_id}</a>` : ""}
        ${next ? `<a class="btn" href="#/plants/${next.plant_id}">${next.plant_id} →</a>` : ""}
        <a class="btn primary" href="#/map?plant=${p.plant_id}">Show on map</a>
      </div>
    </div>
    <div class="plant-grid">
      <div>
        <div class="crops">
          ${crop(s, p, "rgb", "RGB · 6 mm")}
          ${crop(s, p, "ndvi", "NDVI")}
        </div>
        <p class="small muted">1.2 m × 1.2 m around the plant; yellow = detected outline.</p>
        ${neighbours.length ? `<div class="card" style="margin-top:14px"><h3>Along drip line ${esc(p.line_id)}</h3>
          <div class="neighbours">${neighbours.map((q) => `<a class="nb ${q.plant_id === id ? "here" : ""}" href="#/plants/${q.plant_id}">
            <span class="thumb" style="${thumbStyle(s, q.plant_id, "rgb", 64)}"></span>
            <span><span class="dot" style="background:${colorOf(s.healthClasses, q.health_class)}"></span> ${q.plant_id.slice(-4)}</span></a>`).join("")}</div></div>` : ""}
      </div>
      <div class="stack">
        <div class="card">
          <div class="status-line">${planted ? (green ? "Living sapling" : "No green canopy") : "Between-line vegetation"}</div>
          <p class="muted" style="margin:0 0 12px">${planted
            ? (green ? `A planted ${esc(s.summary.declared_species)} position with ${fmt.int(p.canopy_area_m2 * 1e4)} cm² of green canopy.`
              : "The planting spot is there, but no green canopy is visible: the sapling may be dry, dormant or dead. Worth a field check.")
            : "Vegetation growing between planting positions (spontaneous annuals or weeds). Not graded for health."}</p>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <span class="chip"><span class="dot" style="background:${colorOf(s.idClasses, p.plant_class)}"></span>${esc(p.plant_class)}</span>
            ${p.health_class ? `<span class="chip"><span class="dot" style="background:${hColor}"></span>${esc(p.health_class)}</span>` : ""}
            <span class="chip">${green ? "Green canopy" : "No green canopy"}</span>
          </div>
        </div>
        <div class="grid g2">
          <div class="card"><h3>1 · Detection</h3>
            ${kv([["Confidence", fmt.pct(p.detection_confidence)], ["Plant area", `${fmt.n(p.area_m2, 3)} m²`],
              ["Green canopy", `${fmt.int(p.canopy_area_m2 * 1e4)} cm²`], ["Drip line", `${esc(p.line_id ?? "–")} · ${fmt.n(Math.abs(p.dist_to_line_m), 2)} m`],
              ["Found by", p.detection_source === "NDVI vegetation patch" ? "NDVI recall" : "RGB + NDVI"]])}
            ${meter(p.detection_confidence)}
          </div>
          <div class="card"><h3>2 · Identification <span class="badge">Exp.</span></h3>
            ${kv([["Class", esc(p.plant_class)], ["Confidence", fmt.pct(p.identification_confidence)], [`P(planted ${esc(s.summary.declared_species.split(" ")[0])})`, fmt.n(p.p_planted)]])}
            ${meter(p.identification_confidence, colorOf(s.idClasses, p.plant_class))}
          </div>
          <div class="card"><h3>3 · Health <span class="badge">Exp.</span></h3>
            ${p.health_class ? kv([["Condition", esc(p.health_class)], ["Health score", fmt.n(p.health_score)], ["Group probability", fmt.pct(p.health_confidence)]]) : '<p class="muted">Not graded (not a planted position).</p>'}
            ${p.health_score != null ? meter(p.health_score, hColor) : ""}
          </div>
          <div class="card"><h3>NDVI &amp; colour</h3>
            ${kv([["Mean / median NDVI", `${fmt.n(p.mean_ndvi, 2)} / ${fmt.n(p.median_ndvi, 2)}`], ["P10 – P90", `${fmt.n(p.p10_ndvi, 2)} – ${fmt.n(p.p90_ndvi, 2)}`],
              ["Soil around plant", fmt.n(p.bg_ndvi, 2)], ["Above soil", fmt.signed(p.ndvi_contrast, 2)], ["VARI · green cover", `${fmt.n(p.vari, 2)} · ${fmt.pct(p.green_fraction)}`]])}
          </div>
        </div>
        <p class="small muted" style="margin:0">Models: ${esc(M.detection.name)} v${esc(M.detection.version)} · ${esc(M.identification.backbone)} v${esc(M.identification.version)} · health v${esc(M.health.version)}.
          ${esc(M.health.disclaimer)}</p>
      </div>
    </div>`;
}

function spriteStyle(s, id, kind) {
  const i = s.mediaPos.get(id);
  if (i == null || !s.media) return "";
  const { cols } = s.media;
  const per = cols * cols;
  const a = Math.floor(i / per);
  const j = i % per;
  const rows = Math.ceil(Math.min(s.media.ids.length - a * per, per) / cols);
  const r = Math.floor(j / cols);
  const c = j % cols;
  const px = cols > 1 ? (c / (cols - 1)) * 100 : 0;
  const py = rows > 1 ? (r / (rows - 1)) * 100 : 0;
  return `background-image:url('${s.base}media/${s.media[kind][a]}');background-size:${cols * 100}% ${rows * 100}%;background-position:${px}% ${py}%`;
}

function crop(s, p, kind, label) {
  const style = spriteStyle(s, p.plant_id, kind);
  const g = s.geomById.get(p.plant_id);
  let path = "";
  if (g) {
    const half = (s.media?.window_m || 1.2) / 2;
    const pts = g.coordinates[0].map(([lon, lat]) => {
      const [x, y] = toLocal(lon, lat, p.lon, p.lat);
      return `${((x + half) / (2 * half)) * 100},${((half - y) / (2 * half)) * 100}`;
    });
    path = `<svg viewBox="0 0 100 100" preserveAspectRatio="none"><polygon points="${pts.join(" ")}" fill="none" stroke="#ffd166" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;
  }
  return `<div class="crop"><div class="img" style="${style}"></div>${path}<span class="tag">${label}</span></div>`;
}
