import { esc, fmt } from "../ui.js";

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

export function renderOverview(el, s) {
  const st = s.summary.stats;
  const M = s.summary.models;
  const planted = s.list.filter((p) => p.position_type === "Planting line");
  const expected = st.expected_planting_positions ?? st.planted_positions;
  const hc = st.health_counts_planted || {};
  const species = s.summary.declared_species;
  const fig = s.figures?.field_rgb ? `${s.base}figures/field_rgb.webp` : null;
  const worst = [...s.lineAgg].filter((l) => l.n >= 10).sort((a, b) => a.score - b.score).slice(0, 5);

  const steps = [
    { k: "Planting spots", v: expected, d: `expected along ${st.planting_lines} planting lines`, share: 1 },
    { k: "Plants located", v: st.planted_positions, d: `${fmt.pct(st.planted_positions / expected, 1)} of spots · ${fmt.int(st.inferred_missing_positions)} spots empty`, share: st.planted_positions / expected },
    { k: "Green canopy", v: st.green_canopy_planted, d: `${fmt.pct(st.green_canopy_planted / st.planted_positions, 1)} of located plants are green`, share: st.green_canopy_planted / expected },
    { k: "Planted stock", v: st.planted_identified_as_species, d: `${fmt.pct(st.planted_identified_as_species / st.planted_positions, 1)} of located plants look like the planted stock; species per planting record`, share: st.planted_identified_as_species / expected },
    { k: "Need attention", v: (st.no_green_canopy_planted || 0) + (st.inferred_missing_positions || 0), d: `${fmt.int(st.no_green_canopy_planted)} without green canopy + ${fmt.int(st.inferred_missing_positions)} empty spots`, alert: true },
  ];
  const totalHealth = Object.values(hc).reduce((a, b) => a + b, 0) || 1;

  el.innerHTML = `
    <section class="hero">
      <div>
        <div class="eyebrow">Revegetation survey · ${esc(species)}</div>
        <h1>${esc(s.summary.project)}</h1>
        <p class="lede">Every sapling in this ${fmt.n(st.surveyed_area_ha, 2)} ha block, found from drone imagery and assessed one by one:
          where it is, what it is, and how it is doing.</p>
        <div class="facts">
          <span class="chip">${fmt.n(st.surveyed_area_ha, 2)} ha surveyed</span>
          <span class="chip">${st.planting_lines} planting lines · ${fmt.n(st.line_spacing_m, 1)} m apart</span>
          <span class="chip">RGB ${fmt.n(s.summary.data.rgb.pixel_size_m * 1000, 0)} mm · NDVI ${fmt.n(s.summary.data.ndvi.pixel_size_m * 100, 1)} cm</span>
          <span class="chip">Processed ${esc(s.summary.processing_date)}</span>
        </div>
        <div style="display:flex;gap:10px;flex-wrap:wrap">
          <a class="btn primary" href="#/map">Open the map</a>
          <a class="btn" href="#/plants">Browse ${fmt.int(st.planted_positions)} plants</a>
          <a class="btn" href="#/analyze">Analyze a new survey</a>
        </div>
      </div>
      <div class="hero-img">${fig ? `<img src="${fig}" alt="Drone RGB orthomosaic of the ${esc(s.summary.project)} block" />` : ""}
        <span class="cap">RGB orthomosaic · ${fmt.n(st.surveyed_area_ha, 2)} ha</span></div>
    </section>

    ${timelineHtml(s)}

    <section class="section">
      <h2>Plant count</h2>
      <div class="funnel">${steps.map((x) => `
        <div class="fstep ${x.alert ? "alert" : ""}">
          <div class="k">${esc(x.k)}</div>
          <div class="v num">${fmt.int(x.v)}</div>
          <div class="d">${esc(x.d)}</div>
          ${x.share != null ? `<div class="bar"><span style="width:${Math.min(1, x.share) * 100}%"></span></div>` : ""}
        </div>`).join("")}</div>
    </section>

    <section class="section grid g2">
      <div class="card">
        <h3>Plant health <span class="badge">Experimental</span></h3>
        <p class="sub">${fmt.int(totalHealth)} planted saplings in ${s.healthClasses.length} condition groups found by the unsupervised model</p>
        <div class="health-strip">${s.healthClasses.map((c) => `<span title="${esc(c.key)}: ${fmt.int(hc[c.key] || 0)}" style="width:${((hc[c.key] || 0) / totalHealth) * 100}%;background:${c.color}"></span>`).join("")}</div>
        <div class="legend-row">${s.healthClasses.map((c) => `<span><span class="dot" style="background:${c.color}"></span> ${esc(c.key)} <span class="n">${fmt.int(hc[c.key] || 0)}</span></span>`).join("")}</div>
        <p class="small muted" style="margin-bottom:0">Health is inferred from RGB and NDVI remote-sensing indicators; it is not a laboratory disease diagnosis.</p>
      </div>
      <div class="card">
        <h3>Lines needing attention</h3>
        <p class="sub">Drip lines with the lowest average plant condition</p>
        <table class="mini"><thead><tr><th>Line</th><th class="n">Plants</th><th class="n">Poor or worse</th><th class="n">Avg score</th></tr></thead><tbody>
          ${worst.map((l) => `<tr><td><a href="#/plants?line=${l.line_id}">${l.line_id}</a></td><td class="n">${l.n}</td><td class="n">${fmt.pct(l.poorShare)}</td><td class="n">${fmt.n(l.score)}</td></tr>`).join("")}
        </tbody></table>
        <p class="small" style="margin-bottom:0"><a href="#/insights">All lines in Insights →</a></p>
      </div>
    </section>

    <section class="section">
      <h2>How the results were made</h2>
      <div class="grid g3">
        <a class="card model-card" href="#/method">
          <span class="step">1 · Detection</span>
          <span class="big num">${fmt.int(st.planted_positions)}</span>
          <p>plants located with SAM 2.1 on 1.2 cm imagery, anchored to the drip lines and the planting rhythm.</p>
        </a>
        <a class="card model-card" href="#/method">
          <span class="step">2 · Identification <span class="badge">Experimental</span></span>
          <span class="big num">${fmt.pct(st.identification_rate, 0)}</span>
          <p>of plants identified by a DINOv3 satellite-pretrained model${M.identification.spatial_cv ? ` (spatial CV AUC ${fmt.n(M.identification.spatial_cv.roc_auc, 3)})` : ""}.</p>
        </a>
        <a class="card model-card" href="#/method">
          <span class="step">3 · Health <span class="badge">Experimental</span></span>
          <span class="big num">${M.health.k} groups</span>
          <p>unsupervised condition groups from crown embeddings + NDVI, stable at ARI ${fmt.n(M.health.stability_ari_mean, 2)}.</p>
        </a>
      </div>
    </section>

    <section class="section">
      <h2>Processing</h2>
      <ol class="lifecycle">${s.summary.lifecycle.map((x) => `<li class="${x.status}" title="${esc(x.detail)}"><b>${x.status === "done" ? "✓" : "◐"}</b>${esc(x.step)}</li>`).join("")}</ol>
    </section>`;
}
