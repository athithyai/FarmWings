// Survey data: the bundled Pilot survey plus any survey processed by a FarmWings compute
// server. Every screen reads the *current* survey from here.

const BUNDLED = { id: "pilot", name: "Pilot", base: new URL("data/", document.baseURI).href, source: "bundled" };
const listeners = new Set();
const registry = new Map(); // id -> loaded survey
let current = null;

export const SERVER_KEY = "farmwings.server";
export function serverUrl() {
  try { return localStorage.getItem(SERVER_KEY) || "http://127.0.0.1:8765"; } catch { return "http://127.0.0.1:8765"; }
}
export function setServerUrl(u) {
  try { localStorage.setItem(SERVER_KEY, u.replace(/\/+$/, "")); } catch { /* storage unavailable */ }
}

export function onChange(fn) { listeners.add(fn); return () => listeners.delete(fn); }
export function survey() { return current; }
export function surveyById(id) { return registry.get(id); }

async function getJson(url, optional = false) {
  const r = await fetch(url);
  if (!r.ok) {
    if (optional) return null;
    throw new Error(`${url} → ${r.status}`);
  }
  return r.json();
}

// Colours: identification = categorical slots, health = diverging ramp sized to the group count
const HEALTH_RAMPS = {
  3: ["#12805a", "#9a9994", "#c93a3a"],
  4: ["#12805a", "#6cc79f", "#ef9a8a", "#c93a3a"],
  5: ["#12805a", "#6cc79f", "#9a9994", "#ef9a8a", "#c93a3a"],
};
export const VIGOUR = ["Very high vigour", "High vigour", "Moderate vigour", "Low vigour", "Very low vigour"];

async function load(desc) {
  const b = desc.base;
  const [summary, plants, lines, tiles, media, figures] = await Promise.all([
    getJson(b + "summary.json"), getJson(b + "plants.json"), getJson(b + "lines.json"),
    getJson(b + "tiles/index.json"), getJson(b + "media/index.json", true), getJson(b + "figures/index.json", true),
  ]);
  const list = plants.features.map((f) => f.properties);
  const byId = new Map(list.map((p) => [p.plant_id, p]));
  const geomById = new Map(plants.features.map((f) => [f.properties.plant_id, f.geometry]));
  const mediaPos = new Map((media?.ids || []).map((id, i) => [id, i]));
  const healthClasses = summary.models.health.classes;
  const ramp = HEALTH_RAMPS[healthClasses.length] || HEALTH_RAMPS[5];
  const planted = summary.stats.planted_class;
  const s = {
    ...desc, summary, plants, lines, tiles, media, figures, list, byId, geomById, mediaPos,
    idClasses: [
      { key: planted, color: "#3987e5" },
      { key: "Other vegetation", color: "#d95926" },
      { key: "Unclassified", color: "#898781" },
    ],
    healthClasses: healthClasses.map((k, i) => ({ key: k, color: ramp[i] })),
    vigourClasses: VIGOUR.map((k, i) => ({ key: k, color: HEALTH_RAMPS[5][i] })),
    lineAgg: aggregateLines(list),
  };
  registry.set(desc.id, s);
  return s;
}

function aggregateLines(list) {
  const m = new Map();
  for (const p of list) {
    if (p.position_type !== "Planting line" || !p.line_id) continue;
    let a = m.get(p.line_id);
    if (!a) m.set(p.line_id, (a = { line_id: p.line_id, n: 0, score: 0, ndvi: 0, poor: 0, green: 0, ids: [] }));
    a.n += 1;
    a.score += p.health_score ?? 0;
    a.ndvi += p.mean_ndvi ?? 0;
    a.green += p.green_area_m2 ?? 0;
    if (/poor/i.test(p.health_class || "")) a.poor += 1;
    a.ids.push(p.plant_id);
  }
  return [...m.values()].map((a) => ({ ...a, score: a.score / a.n, ndvi: a.ndvi / a.n, poorShare: a.poor / a.n }))
    .sort((x, y) => x.line_id.localeCompare(y.line_id));
}

export async function useSurvey(desc) {
  const s = registry.get(desc.id) || (await load(desc));
  current = s;
  try { localStorage.setItem("farmwings.survey", JSON.stringify({ id: desc.id, name: desc.name, base: desc.base, source: desc.source })); } catch { /* ignore */ }
  listeners.forEach((fn) => fn(s));
  return s;
}

export async function initialSurvey() {
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem("farmwings.survey") || "null"); } catch { /* ignore */ }
  if (saved && saved.id !== "pilot") {
    try { return await useSurvey(saved); } catch { /* server gone: fall back to the Pilot */ }
  }
  return useSurvey(BUNDLED);
}

export const bundled = BUNDLED;

export function jobSurvey(server, job) {
  return { id: `job:${job.id}`, name: job.name, base: `${server}/api/jobs/${job.id}/data/`, source: "server", job };
}

// Thumbnail sprite style for a plant (RGB or NDVI atlas)
export function thumbStyle(s, plantId, kind = "rgb", size = 48) {
  const i = s.mediaPos.get(plantId);
  if (i == null || !s.media) return "";
  const { cols, thumb } = s.media;
  const per = cols * cols;
  const file = s.media[kind][Math.floor(i / per)];
  const j = i % per;
  const r = Math.floor(j / cols);
  const c = j % cols;
  const k = size / thumb;
  const rows = Math.min(cols, Math.ceil((Math.min(s.media.ids.length - Math.floor(i / per) * per, per)) / cols));
  return `background-image:url('${s.base}media/${file}');background-size:${cols * thumb * k}px ${rows * thumb * k}px;` +
    `background-position:-${c * thumb * k}px -${r * thumb * k}px;width:${size}px;height:${size}px`;
}
