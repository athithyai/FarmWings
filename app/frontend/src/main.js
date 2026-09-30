import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
// MapLibre v6 loads its web worker as a separate module; let Vite bundle it.
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import "./style.css";
import "./tiles.js";
import { BUNDLED_SURVEYS, api, initialSurvey, jobSurvey, onChange, serverUrl, survey, useSurvey } from "./store.js";
import { esc } from "./ui.js";
import { hideTip } from "./charts.js";
import { renderOverview } from "./views/overview.js";
import { renderPlants } from "./views/plants.js";
import { renderPlant } from "./views/plant.js";
import { renderInsights } from "./views/insights.js";
import { renderMethod } from "./views/method.js";
import { renderAnalyze } from "./views/analyze.js";
import { showMap, hideMap } from "./views/map.js";

maplibregl.setWorkerUrl(workerUrl);

const screen = document.getElementById("screen");
const mapScreen = document.getElementById("map-screen");

function parse() {
  const h = (location.hash || "#/").slice(1);
  const [path, query] = h.split("?");
  const parts = path.split("/").filter(Boolean);
  return { parts, params: new URLSearchParams(query || "") };
}

const TITLES = { overview: "Overview", plants: "Plants", insights: "Insights", method: "Methodology", analyze: "Analyze survey" };

async function route() {
  hideTip();
  const s = survey();
  const { parts, params } = parse();
  const name = parts[0] || "overview";
  document.querySelectorAll(".nav a").forEach((a) => {
    if (a.dataset.route === name) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  if (name === "map") {
    screen.hidden = true;
    mapScreen.hidden = false;
    showMap(s, params);
    document.title = `Map · ${s.summary.project} · FarmWings`;
    return;
  }
  hideMap();
  mapScreen.hidden = true;
  screen.hidden = false;
  screen.innerHTML = "";
  if (name === "plants" && parts[1]) renderPlant(screen, s, decodeURIComponent(parts[1]));
  else if (name === "plants") renderPlants(screen, s, params);
  else if (name === "insights") renderInsights(screen, s);
  else if (name === "method") renderMethod(screen, s);
  else if (name === "analyze") renderAnalyze(screen);
  else renderOverview(screen, s);
  document.title = `${name === "plants" && parts[1] ? decodeURIComponent(parts[1]) : TITLES[name] || "Overview"} · FarmWings`;
  window.scrollTo(0, 0);
}

// ---- survey switcher: the bundled Pilot + finished surveys on the compute server
const select = document.getElementById("survey-select");
async function refreshSurveys() {
  const opts = BUNDLED_SURVEYS.map((b) => ({ value: b.id, label: b.name, bundled: b }));
  try {
    const r = await api("/api/jobs", { signal: AbortSignal.timeout(2500) });
    if (r.ok) {
      for (const j of await r.json()) if (j.status === "done") opts.push({ value: `job:${j.id}`, label: j.name, job: j });
    }
  } catch { /* no compute server running: only the bundled survey */ }
  const cur = survey()?.id;
  select.innerHTML = opts.map((o) => `<option value="${esc(o.value)}" ${o.value === cur ? "selected" : ""}>${esc(o.label)}</option>`).join("");
  select.onchange = async () => {
    const o = opts.find((x) => x.value === select.value);
    await useSurvey(o.bundled || jobSurvey(serverUrl(), o.job));
  };
}
export { refreshSurveys };
window.farmwings = { refreshSurveys, survey };

async function boot() {
  screen.innerHTML = `<p class="muted">Loading survey…</p>`;
  try {
    await initialSurvey();
  } catch (e) {
    screen.innerHTML = `<div class="card"><h3>Could not load the survey</h3><p class="muted">${esc(e.message)}</p></div>`;
    return;
  }
  await refreshSurveys();
  onChange(() => route());
  window.addEventListener("hashchange", route);
  route();
}
boot();
