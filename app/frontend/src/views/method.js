import { esc, fmt } from "../ui.js";
import { detectionFlow, flowLegend, healthFlow, identificationFlow } from "../diagrams.js";

export function renderMethod(el, s) {
  const M = s.summary.models;
  const st = s.summary.stats;
  const fig = (name, alt) => (s.figures?.[name] ? `<div class="fig"><img loading="lazy" src="${s.base}figures/${name}.webp" alt="${esc(alt)}" /></div>` : "<div></div>");
  const trial = M.identification.trial;
  const trialRows = trial ? Object.entries(trial).filter(([, v]) => v && typeof v === "object" && "roc_auc" in v)
    .sort((a, b) => b[1].roc_auc - a[1].roc_auc).slice(0, 8) : [];
  const v = s.summary.validation;
  const atSpots = s.list.filter((p) => p.position_type === "Planting line");
  const nCls = (k) => atSpots.filter((p) => p.plant_class === k).length;
  const idCounts = { n: atSpots.length, planted: nCls(st.planted_class), other: nCls("Other vegetation"), uncl: nCls("Unclassified") };

  el.innerHTML = `
    <div class="page-head"><div><div class="eyebrow">Methodology</div><h1>How FarmWings reads a field</h1>
      <p>Three separate models run on every survey: where each plant is, what it is, and how it is doing. Each result keeps the model and version that produced it.</p>${flowLegend}</div></div>

    <div class="steps">
      <div class="card stepcard">
        <div><div class="eyebrow">Input</div><h3>Drone RGB + NDVI</h3>
          <p>An RGB orthomosaic (${fmt.n(s.summary.data.rgb.pixel_size_m * 1000, 0)} mm) and an NDVI raster (${fmt.n(s.summary.data.ndvi.pixel_size_m * 100, 1)} cm) in ${esc(s.summary.data.crs)}.
          The two are put on one grid and NDVI is co-registered to the RGB content (they were offset by up to 7 cm).</p>
          <ul><li>${st.drip_lines ?? st.planting_lines} drip lines found, ${fmt.n(st.line_spacing_m, 2)} m apart; ${st.planting_lines} of them carry a planting row (edge and feeder pipes do not).</li></ul></div>
        ${fig("field_ndvi", "NDVI of the survey")}
      </div>

      <div class="card stepcard">
        <div class="flow-row">${detectionFlow(st)}</div>
        <div><div class="eyebrow">Model 1</div><h3>Plant detection</h3>
          <p>Candidate spots come from RGB darkness plus NDVI above the local soil. <b>SAM 2.1</b> outlines each one on 1.2 cm imagery, and green NDVI patches that were missed get their own prompt.</p>
          <ul>
            <li>A plant counts as <b>planted</b> only on a visible drip line.</li>
            <li>One plant per planting spot: along each line the objects that best fit the ~${fmt.n(st.line_spacing_m, 1)} m planting rhythm are chosen; leaf fragments are merged, weeds between spots are set aside.</li>
            <li>Expected spots without a plant are reported as <b>empty spots</b> (${fmt.int(st.inferred_missing_positions)}).</li>
            <li>Tested and rejected: DeepForest (0 detections; trained on large tree crowns), SAM automatic masks (unselective).</li>
          </ul>
          ${v ? `<p class="note">Checked against the project's installation record (5,684 planting points, used as a reference, not as ground truth): ${fmt.pct(v.precision, 1)} of FarmWings plants sit on a recorded spot and ${fmt.pct(v.recall, 1)} of recorded spots have a FarmWings plant, median offset ${fmt.int(v.median_distance_m * 100)} cm.</p>` : ""}
        </div>
        ${fig("detection", "Detected plants on the imagery")}
      </div>

      <div class="card stepcard">
        <div class="flow-row">${identificationFlow(idCounts)}</div>
        <div><div class="eyebrow">Model 2 · Experimental</div><h3>Plant identification</h3>
          <p>Each plant's 6 mm crop (drip line removed, background masked) is embedded with <b>${esc(M.identification.backbone)}</b>, a satellite-pretrained foundation model, and combined with NDVI, colour and shape in a small classifier.</p>
          <ul>
            <li>Labels without hand annotation: plants on drip lines are the planted stock; vegetation between lines is not.</li>
            <li>Classes: ${M.identification.classes.map(esc).join(", ")}; below ${fmt.pct(M.identification.min_confidence)} confidence a plant is Unclassified.</li>
            <li>The species name comes from the project record; it is not confirmable from imagery at sapling size.</li>
            <li>Built with DINOv3 (Meta, DINOv3 License).</li>
            ${M.identification.spatial_cv ? `<li>Spatial cross-validation: AUC ${fmt.n(M.identification.spatial_cv.roc_auc, 3)}, balanced accuracy ${fmt.n(M.identification.spatial_cv.balanced_accuracy, 3)}.</li>` : ""}
          </ul>
          ${trialRows.length ? `<table class="mini"><thead><tr><th>Inputs tested</th><th class="n">AUC</th></tr></thead><tbody>${trialRows.map(([k, x]) =>
            `<tr class="${k.includes("sat493m_crown+tabular") ? "best" : ""}"><td>${esc(k.replaceAll("_", " "))}</td><td class="n">${fmt.n(x.roc_auc, 3)}</td></tr>`).join("")}</tbody></table>` : ""}
        </div>
        ${fig("identification", "Identification examples")}
      </div>

      <div class="card stepcard">
        <div class="flow-row">${healthFlow(st, s.healthClasses)}</div>
        <div><div class="eyebrow">Model 3 · Experimental</div><h3>Plant health</h3>
          <p>There is no validated pretrained health model for young desert saplings, and no field health labels, so the model is <b>unsupervised</b>: a Gaussian mixture on the crown embedding plus NDVI and colour indicators groups the planted saplings, and the groups are named by their measured profile.</p>
          <table class="mini"><thead><tr><th>Group</th><th class="n">Plants</th><th>Profile</th></tr></thead><tbody>
          ${M.health.groups.map((g) => `<tr><td><span class="dot" style="background:${s.healthClasses.find((c) => c.key === g.name)?.color}"></span> ${esc(g.name)}</td><td class="n">${fmt.int(g.n_plants)}</td><td class="small">${esc(g.profile)}</td></tr>`).join("")}
          </tbody></table>
          <ul><li>Stable on resampling (ARI ${fmt.n(M.health.stability_ari_mean, 2)}); agrees with the transparent NDVI/RGB vigour index (ρ ${fmt.n(M.health.spearman_score_vs_vigour_index, 2)}).</li>
            <li>Green canopy = NDVI at least ${M.health.canopy_rule?.dndvi_above_soil ?? 0.2} above the plant's own soil.</li></ul>
          <p class="note">${esc(M.health.disclaimer)}</p>
        </div>
        ${fig("health_groups", "Example crops per health group")}
      </div>
    </div>

    <div class="section card">
      <h3>Limits to keep in mind</h3>
      <ul class="muted" style="margin:0;padding-left:18px">
        <li>No field-verified plant list exists, so detection accuracy is estimated from the installation record and visual audits, not measured against ground truth.</li>
        <li>Health is relative to this survey and date; low NDVI can mean stress, dormancy, small size or a dead plant.</li>
        <li>One survey date; repeat flights will turn condition into change over time.</li>
      </ul>
    </div>`;
}
