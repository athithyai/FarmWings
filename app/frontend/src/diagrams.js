// Model diagrams: how each of the three models turns imagery into a result. Used on the landing
// page (generic) and in Methodology (with the survey's numbers). Plain HTML so they reflow to a
// vertical flow on narrow screens.
import { esc, fmt } from "./ui.js";

const HEALTH_DEFAULT = [
  { key: "Very good", color: "#12805a" }, { key: "Good", color: "#6cc79f" }, { key: "Fair", color: "#9a9994" },
  { key: "Poor", color: "#ef9a8a" }, { key: "Very poor", color: "#c93a3a" },
];

const node = (kind, title, sub, tag, extra = "") =>
  `<div class="fn ${kind}">${tag ? `<span class="fn-tag">${esc(tag)}</span>` : ""}<b>${esc(title)}</b>${sub ? `<span class="fn-sub">${esc(sub)}</span>` : ""}${extra}</div>`;
const arrow = `<span class="fa" aria-hidden="true"></span>`;
const lanes = (...rows) => `<div class="flow-lanes">${rows.map((r) => `<div class="lane">${r.join(arrow)}</div>`).join("")}</div>`;
const flow = (label, parts) => `<div class="flow-box"><div class="flow" role="img" aria-label="${esc(label)}">${parts.join(arrow)}</div></div>`;

export const flowLegend = `<div class="flow-legend">
  <span><i class="k data"></i>Input data</span><span><i class="k step"></i>Processing step</span>
  <span><i class="k model"></i>AI model</span><span><i class="k out"></i>Result</span></div>`;

export function detectionFlow(st) {
  const out = st
    ? `${fmt.int(st.planted_positions)} planted · ${fmt.int(st.inferred_missing_positions)} empty spots · ${fmt.int(st.between_line_vegetation)} between lines`
    : "Planted, empty spots, between-line plants";
  return flow("Plant detection: drone RGB and NDVI, drip lines and candidate spots, SAM 2.1 outlines, planting-rhythm check, plant record", [
    lanes([node("data", "Drone RGB", "6 mm orthomosaic")], [node("data", "NDVI", "2.4 cm, aligned to RGB")]),
    node("step", "Drip lines + spots", "Planting lines, dark and green spots"),
    node("model", "SAM 2.1", "Outlines every plant on 1.2 cm", "AI model · Meta"),
    node("step", "Planting rhythm", "One plant per ~2 m spot"),
    node("out", "Plant record", out),
  ]);
}

export function identificationFlow(st, planted = "planted species") {
  const out = st
    ? `${fmt.int(st.class_counts?.[st.planted_class])} ${planted.replace(" (planted)", "")} · ${fmt.int(st.class_counts?.["Other vegetation"])} other · ${fmt.int(st.class_counts?.Unclassified)} unclassified`
    : "Planted species, other vegetation or unclassified";
  return flow("Plant identification: plant crop to DINOv3 satellite model, plus NDVI colour and shape, into a classifier", [
    lanes([node("data", "Plant crop", "6 mm, drip line removed"), node("model", "DINOv3 SAT-493M", "Describes how the crown looks", "AI model · Meta")],
      [node("data", "NDVI, colour, shape", "Per-plant measurements")]),
    node("model", "Classifier", "Trained on the planting layout", "AI model"),
    node("out", "Identity", out),
  ]);
}

export function healthFlow(st, classes) {
  const cls = classes?.length ? classes.map((c) => ({ key: c.key.replace(" condition", ""), color: c.color, n: st?.health_counts_planted?.[c.key] })) : HEALTH_DEFAULT;
  const total = cls.reduce((a, c) => a + (c.n || 0), 0);
  const bar = `<div class="fn-bar">${cls.map((c) => `<span style="flex:${total ? c.n : 1};background:${c.color}" title="${esc(c.key)}${c.n != null ? `: ${fmt.int(c.n)}` : ""}"></span>`).join("")}</div>
    <div class="fn-keys">${cls.map((c) => `<span><i style="background:${c.color}"></i>${esc(c.key)}${c.n != null ? ` <b class="num">${fmt.int(c.n)}</b>` : ""}</span>`).join("")}</div>`;
  return flow("Plant health: plant crop to DINOv3, NDVI and colour to six indicators, a Gaussian mixture model groups the saplings, groups ranked into condition classes", [
    lanes([node("data", "Plant crop", "6 mm RGB"), node("model", "DINOv3 SAT-493M", "Describes how the crown looks", "AI model · Meta")],
      [node("data", "NDVI + colour", "Plant and its own soil"), node("step", "6 health indicators", "NDVI, contrast, green canopy")]),
    node("model", "Gaussian mixture", "Groups similar saplings, no labels needed", "AI model · unsupervised"),
    node("step", "Rank groups", "By NDVI and green canopy"),
    node("out", "Condition", "", null, bar),
  ]);
}
