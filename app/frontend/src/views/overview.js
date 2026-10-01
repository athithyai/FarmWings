import { thumbStyle } from "../store.js";
import { hideTip, showTip } from "../charts.js";
import { selQuery } from "../select.js";
import { esc, fmt, navigate, toLocal } from "../ui.js";

// Project timeline: public/data/timeline.json per survey; surveys without one get the generic
// steps with only the FarmWings dates (from the summary) filled in.
const DEFAULT_STEPS = [
  ["requested", "Requested", "Client", "Survey requested: the block, the planted species and the questions to answer"],
  ["prep", "Data capture prep", "Drone team", "Flight plan, sensors (RGB + multispectral), ground control, weather window"],
  ["permit", "Permit application", "Drone team", "Flight and site-access permits"],
  ["flight", "Drone flight", "Drone team", "RGB and multispectral capture over the block"],
  ["postprocessing", "Post-processing", "Drone team", "Orthomosaic and NDVI built and delivered as GeoTIFFs"],
  ["insights", "Insight generation", "FarmWings", "Detection, identification and health for every plant"],
  ["complete", "Complete", "FarmWings", "Results published in the FarmWings workspace"],
];
const day = (d) => new Date(`${d}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
const short = (d) => new Date(`${d}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short" });

function timelineSteps(s) {
  if (s.timeline?.steps?.length) return s.timeline.steps;
  const done = s.summary.processing_date;
  return DEFAULT_STEPS.map(([key, label, owner, detail]) => ({
    key, label, owner, detail, status: "done",
    date: key === "insights" || key === "complete" ? done : null,
  }));
}

function timelineHtml(s) {
  const steps = timelineSteps(s);
  const when = (x) => x.start && x.end ? (x.start === x.end ? day(x.start) : `${short(x.start)} – ${day(x.end)}`)
    : x.date ? `${x.date_label ? `${x.date_label} ` : ""}${day(x.date)}` : null;
  const last = [...steps].reverse().find((x) => x.status === "done");
  const allDone = steps.every((x) => x.status === "done");
  const cur = steps.find((x) => x.status === "current");
  const lastDate = last && (last.end || last.date);
  return `
    <section class="section timeline-card card">
      <div class="tl-head"><h2>Project timeline</h2>
        <span class="chip ${allDone ? "ok" : ""}">${allDone ? `Complete${lastDate ? ` · ${day(lastDate)}` : ""}` : `In progress${cur ? `: ${esc(cur.label)}` : ""}`}</span></div>
      <ol class="timeline">${steps.map((x, i) => {
        const w = when(x);
        return `<li class="tl ${x.status}" title="${esc(x.detail || "")}">
          <span class="tl-dot" aria-hidden="true">${x.status === "done" ? "✓" : i + 1}</span>
          <b>${esc(x.label)}</b>
          <span class="tl-date ${w ? "" : "none"}">${w ? esc(w) : "Date not recorded"}</span>
          <span class="tl-owner">${esc(x.owner || "")}</span>
          <span class="tl-detail">${esc(x.detail || "")}</span>
        </li>`;
      }).join("")}</ol>
    </section>`;
}


// ------------------------------------------------------------------ the story
// The Overview tells the survey as five short chapters. A canvas on the side draws every planting
// spot of this survey and re-colours as the reader scrolls: the field, the count, signs of life,
// condition, where to go. Hover a dot to meet that plant; click to open it.

const MODES = ["block", "found", "canopy", "health", "act"];
const hex = (h) => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];

export function renderOverview(el, s) {
  const st = s.summary.stats;
  const planted = s.list.filter((p) => p.position_type === "Planting line");
  const expected = st.expected_planting_positions ?? planted.length;
  const empty = s.gaps.features.length;
  const green = planted.filter((p) => p.living_canopy).length;
  const noGreen = planted.length - green;
  const attention = noGreen + empty;
  const hc = {};
  for (const p of planted) hc[p.health_class] = (hc[p.health_class] || 0) + 1;
  const maxH = Math.max(1, ...Object.values(hc));
  const perLine = {};
  for (const p of planted) if (!p.living_canopy) perLine[p.line_id] = (perLine[p.line_id] || 0) + 1;
  for (const g of s.gaps.features) perLine[g.properties.line_id] = (perLine[g.properties.line_id] || 0) + 1;
  const topLines = Object.entries(perLine).sort((a, b) => b[1] - a[1]).slice(0, 3);
  const ha = fmt.n(st.surveyed_area_ha, 1);
  const best = [...planted].filter((p) => p.living_canopy && s.mediaPos.has(p.plant_id)).sort((a, b) => (b.health_score ?? 0) - (a.health_score ?? 0)).slice(0, 4);
  const worst = [...planted].filter((p) => !p.living_canopy && s.mediaPos.has(p.plant_id)).sort((a, b) => (a.health_score ?? 1) - (b.health_score ?? 1)).slice(0, 4);
  const thumb = (p, tag) => `<a class="meet" href="#/plants/${esc(p.plant_id)}"><span class="thumb" style="${thumbStyle(s, p.plant_id, "rgb", 132)}"></span>
    <span class="meet-id num">${esc(p.plant_id)}</span><span class="meet-tag">${esc(tag(p))}</span></a>`;

  el.innerHTML = `
  <section class="story-hero">
    <div class="eyebrow">${esc(s.summary.project)} · ${esc(s.summary.declared_species)}</div>
    <h1>${fmt.int(planted.length)} saplings.<br><em>Each one accounted for.</em></h1>
    <p class="lede">${ha} hectares, ${fmt.int(st.planting_lines)} planting lines, one drone flight. Here is what the field told us.</p>
    <div class="hero-figs">
      <div><b class="num">${fmt.int(expected)}</b><span>planting spots</span></div>
      <div><b class="num">${fmt.pct(planted.length / (expected || 1), 1)}</b><span>hold a plant</span></div>
      <div><b class="num">${fmt.pct(green / (planted.length || 1), 0)}</b><span>green and growing</span></div>
      <div class="alert"><b class="num">${fmt.int(attention)}</b><span>to visit first</span></div>
    </div>
    <span class="scroll-cue" aria-hidden="true">Scroll the story</span>
  </section>

  <section class="story">
    <div class="story-stage"><div class="stage-inner">
      <canvas id="story-canvas" role="img" aria-label="Every planting spot of ${esc(s.summary.project)}"></canvas>
      <div class="stage-key" id="stage-key"></div>
      <div class="stage-hint">Hover a dot to meet the plant</div>
    </div></div>
    <div class="story-chapters">
      <article class="chapter" data-mode="block">
        <span class="ch-n num">01 · The field</span>
        <h2>${fmt.int(expected)} planting spots</h2>
        <p>${ha} hectares of desert, ${fmt.int(st.planting_lines)} drip lines ${fmt.n(st.line_spacing_m, 0)} m apart. Every dot is a place where a sapling was planted.</p>
      </article>
      <article class="chapter" data-mode="found">
        <span class="ch-n num">02 · The count</span>
        <h2>${fmt.int(planted.length)} found.<br>${fmt.int(empty)} missing.</h2>
        <p>Every sapling located to the centimetre. The rings mark the ${fmt.int(empty)} spots where nothing grows any more.</p>
      </article>
      <article class="chapter" data-mode="canopy">
        <span class="ch-n num">03 · Signs of life</span>
        <h2>${fmt.int(green)} are green.</h2>
        <p>${fmt.int(noGreen)} saplings show no green canopy: dry, dormant or lost. They still stand in their spots, which makes them easy to miss on the ground.</p>
      </article>
      <article class="chapter" data-mode="health">
        <span class="ch-n num">04 · Condition</span>
        <h2>Not all green is equal.</h2>
        <p>Every sapling is graded, from very good to very poor.</p>
        <div class="ch-bars">${s.healthClasses.map((c) => `<a href="#/map?${selQuery({ kind: "health", value: c.key })}" class="ch-bar">
          <span class="ch-bar-l">${esc(c.key.replace(" condition", ""))}</span>
          <span class="ch-bar-t"><span style="width:${((hc[c.key] || 0) / maxH) * 100}%;background:${c.color}"></span></span>
          <b class="num">${fmt.int(hc[c.key] || 0)}</b></a>`).join("")}</div>
      </article>
      <article class="chapter" data-mode="act">
        <span class="ch-n num">05 · Where to go</span>
        <h2>${fmt.int(attention)} spots to visit first.</h2>
        <p>They cluster: three drip lines hold ${fmt.int(topLines.reduce((a, l) => a + l[1], 0))} of them.</p>
        <div class="ch-lines">${topLines.map(([l, n]) => `<a href="#/map?${selQuery({ kind: "line", value: l })}"><b>${esc(l)}</b><span class="num">${fmt.int(n)} spots</span><span class="arrow">→</span></a>`).join("")}</div>
        <div class="ch-cta"><a class="btn primary" href="#/map?${selQuery({ kind: "attention", value: "" })}">See them on the map</a>
          <a class="btn" href="#/insights?${selQuery({ kind: "attention", value: "" })}">Plan the visit</a></div>
      </article>
    </div>
  </section>

  ${best.length || worst.length ? `<section class="section closeup">
    <div class="eyebrow">Up close</div><h2>Meet the field</h2>
    <div class="meet-row"><div><h3>Thriving</h3><div class="meet-grid">${best.map((p) => thumb(p, (q) => q.health_class.replace(" condition", ""))).join("")}</div></div>
      <div><h3>Needs a look</h3><div class="meet-grid">${worst.map((p) => thumb(p, () => "No green canopy")).join("")}</div></div></div>
  </section>` : ""}

  ${timelineHtml(s)}

  <section class="section next-row">
    <a class="next-card" href="#/map"><span class="eyebrow">Map</span><b>Fly over every plant</b><span>Colour by condition, select a group, draw an area.</span></a>
    <a class="next-card" href="#/plants"><span class="eyebrow">Plants</span><b>One list, ${fmt.int(s.list.length)} records</b><span>Filter, sort, export, open any plant.</span></a>
    <a class="next-card" href="#/insights"><span class="eyebrow">Insights</span><b>Plan the field visit</b><span>Line by line, all charts connected.</span></a>
  </section>`;

  startStage(el, s, planted, { empty, noGreen, attention });
}

function startStage(el, s, planted, n) {
  const canvas = el.querySelector("#story-canvas");
  const keyBox = el.querySelector("#stage-key");
  const ctx = canvas.getContext("2d");
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const gaps = s.gaps.features.map((g) => ({ lon: g.geometry.coordinates[0], lat: g.geometry.coordinates[1] }));
  const all = planted.concat(gaps);
  if (!all.length) return;

  // local metres, rotated so the block's long axis runs across the canvas
  const lon0 = all.reduce((a, p) => a + p.lon, 0) / all.length;
  const lat0 = all.reduce((a, p) => a + p.lat, 0) / all.length;
  const xy = all.map((p) => toLocal(p.lon, p.lat, lon0, lat0));
  let sxx = 0, syy = 0, sxy = 0;
  for (const [x, y] of xy) { sxx += x * x; syy += y * y; sxy += x * y; }
  const th = 0.5 * Math.atan2(2 * sxy, sxx - syy);
  const rot = xy.map(([x, y]) => [x * Math.cos(-th) - y * Math.sin(-th), x * Math.sin(-th) + y * Math.cos(-th)]);
  const nP = planted.length;

  const hcol = new Map(s.healthClasses.map((c) => [c.key, hex(c.color)]));
  const NEUTRAL = [150, 140, 118];
  const FAINT = [205, 199, 184];
  function target(mode) {
    const out = new Float32Array(all.length * 5);   // r, g, b, dot alpha, ring alpha
    for (let i = 0; i < all.length; i++) {
      const p = all[i];
      const gap = i >= nP;
      let c = NEUTRAL, a = 0.85, ring = 0;
      if (mode === "found") { c = gap ? [255, 79, 123] : [214, 160, 40]; a = gap ? 0 : 0.95; ring = gap ? 1 : 0; }
      if (mode === "canopy") { c = gap ? FAINT : p.living_canopy ? [76, 175, 80] : [224, 106, 90]; a = gap ? 0.4 : 0.95; }
      if (mode === "health") { c = gap ? FAINT : hcol.get(p.health_class) || NEUTRAL; a = gap ? 0.3 : 0.95; }
      if (mode === "act") {
        const hot = gap || !p.living_canopy;
        c = hot ? (gap ? [255, 79, 123] : [224, 90, 70]) : FAINT; a = hot ? (gap ? 0 : 1) : 0.45; ring = gap ? 1 : 0;
      }
      out.set([c[0], c[1], c[2], a, ring], i * 5);
    }
    return out;
  }
  const KEYS = {
    block: [["#968c76", "Planting spot"]],
    found: [["#d6a028", `Plant found`], ["#ff4f7b", `Empty spot (${fmt.int(n.empty)})`, true]],
    canopy: [["#4caf50", "Green canopy"], ["#e06a5a", `No green canopy (${fmt.int(n.noGreen)})`]],
    health: s.healthClasses.map((c) => [c.color, c.key.replace(" condition", "")]),
    act: [["#e05a46", "No green canopy"], ["#ff4f7b", "Empty spot", true]],
  };

  let cur = target("block");
  let from = cur, to = cur, t0 = 0, mode = "block";
  let scale = 1, ox = 0, oy = 0, r = 1.5, W = 0, H = 0;
  const screen = new Float32Array(all.length * 2);

  function layout() {
    const box = canvas.parentElement.getBoundingClientRect();
    W = Math.max(200, box.width);
    H = Math.max(200, box.height - keyBox.offsetHeight - 8);
    const dpr = window.devicePixelRatio || 1;
    canvas.width = W * dpr; canvas.height = H * dpr;
    canvas.style.width = `${W}px`; canvas.style.height = `${H}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const xs = rot.map((q) => q[0]);
    const ys = rot.map((q) => q[1]);
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    const pad = 18;
    scale = Math.min((W - 2 * pad) / (x1 - x0 || 1), (H - 2 * pad) / (y1 - y0 || 1));
    ox = (W - (x1 - x0) * scale) / 2 - x0 * scale;
    oy = (H - (y1 - y0) * scale) / 2 + y1 * scale;
    r = Math.max(1.1, Math.min(3.2, scale * 0.42));
    rot.forEach(([x, y], i) => { screen[i * 2] = ox + x * scale; screen[i * 2 + 1] = oy - y * scale; });
    draw();
  }

  function draw() {
    ctx.clearRect(0, 0, W, H);
    for (let i = 0; i < all.length; i++) {
      const k = i * 5;
      const x = screen[i * 2], y = screen[i * 2 + 1];
      if (cur[k + 3] > 0.01) {
        ctx.fillStyle = `rgba(${cur[k] | 0},${cur[k + 1] | 0},${cur[k + 2] | 0},${cur[k + 3]})`;
        ctx.beginPath(); ctx.arc(x, y, r, 0, 6.2832); ctx.fill();
      }
      if (cur[k + 4] > 0.01) {
        ctx.strokeStyle = `rgba(${cur[k] | 0},${cur[k + 1] | 0},${cur[k + 2] | 0},${cur[k + 4]})`;
        ctx.lineWidth = 1.6;
        ctx.beginPath(); ctx.arc(x, y, r * 2.4, 0, 6.2832); ctx.stroke();
      }
    }
  }

  function tick(now) {
    const f = Math.min(1, (now - t0) / 750);
    const e = f < 0.5 ? 2 * f * f : 1 - (-2 * f + 2) ** 2 / 2;
    cur = new Float32Array(from.length);
    for (let i = 0; i < from.length; i++) cur[i] = from[i] + (to[i] - from[i]) * e;
    draw();
    if (f < 1 && document.body.contains(canvas)) requestAnimationFrame(tick);
  }

  function setMode(m) {
    if (m === mode) return;
    mode = m;
    from = cur; to = target(m); t0 = performance.now();
    keyBox.innerHTML = KEYS[m].map(([c, l, ring]) => `<span><i class="${ring ? "ring" : ""}" style="${ring ? `border-color:${c}` : `background:${c}`}"></i>${esc(l)}</span>`).join("");
    if (reduce) { cur = to; draw(); } else requestAnimationFrame(tick);
  }
  keyBox.innerHTML = KEYS.block.map(([c, l]) => `<span><i style="background:${c}"></i>${esc(l)}</span>`).join("");

  // hover: meet the nearest plant
  function nearest(ev) {
    const b = canvas.getBoundingClientRect();
    const mx = ev.clientX - b.left, my = ev.clientY - b.top;
    let best = -1, bd = 81;
    for (let i = 0; i < nP; i++) {
      const dx = screen[i * 2] - mx, dy = screen[i * 2 + 1] - my;
      const d = dx * dx + dy * dy;
      if (d < bd) { bd = d; best = i; }
    }
    return best >= 0 ? planted[best] : null;
  }
  canvas.addEventListener("mousemove", (ev) => {
    const p = nearest(ev);
    canvas.style.cursor = p ? "pointer" : "";
    if (!p) { hideTip(); return; }
    showTip(`<div class="tip-plant"><span class="thumb" style="${thumbStyle(s, p.plant_id, "rgb", 72)}"></span><div><b class="num">${esc(p.plant_id)}</b><br>
      ${esc(p.living_canopy ? (p.health_class || "Green canopy").replace(" condition", "") : "No green canopy")}<br><span class="muted">line ${esc(p.line_id)} · click to open</span></div></div>`, ev);
  });
  canvas.addEventListener("mouseleave", hideTip);
  canvas.addEventListener("click", (ev) => { const p = nearest(ev); if (p) { hideTip(); navigate(`#/plants/${p.plant_id}`); } });

  const io = new IntersectionObserver((ents) => {
    for (const e of ents) if (e.isIntersecting) setMode(e.target.dataset.mode);
  }, { rootMargin: "-45% 0px -45% 0px" });
  el.querySelectorAll(".chapter").forEach((c) => io.observe(c));
  const ro = new ResizeObserver(() => { if (document.body.contains(canvas)) layout(); else { ro.disconnect(); io.disconnect(); } });
  ro.observe(canvas.parentElement);
  layout();
  void MODES;
}
