import "maplibre-gl/dist/maplibre-gl.css";
import "./tiles.js"; // first: configures the MapLibre worker and the tile protocol
import "./style.css";
import { BUNDLED_SURVEYS, api, initialSurvey, jobSurvey, onChange, serverUrl, survey, useSurvey } from "./store.js";
import { esc, navigate } from "./ui.js";
import { hideTip } from "./charts.js";
import { initials, signOut, user } from "./session.js";
import { renderLanding } from "./views/landing.js";
import { renderSignin } from "./views/signin.js";
import { renderProjects } from "./views/projects.js";
import { renderOverview } from "./views/overview.js";
import { renderPlants } from "./views/plants.js";
import { renderPlant } from "./views/plant.js";
import { renderInsights } from "./views/insights.js";
import { renderAnalyze } from "./views/analyze.js";
import { showMap, hideMap } from "./views/map.js";

const screen = document.getElementById("screen");
const mapScreen = document.getElementById("map-screen");
const header = document.querySelector(".appbar");

// Public: landing + sign-in. Everything else needs a session (Google or demo).
const WORKSPACE = new Set(["overview", "map", "plants", "insights", "analyze"]);
const TITLES = { overview: "Overview", plants: "Plants", insights: "Insights", analyze: "Analyze survey", projects: "Projects", signin: "Sign in" };

function parse() {
  const h = (location.hash || "#/").slice(1);
  const [path, query] = h.split("?");
  const parts = path.split("/").filter(Boolean);
  return { parts, params: new URLSearchParams(query || "") };
}

let cleanup = null;
let surveyReady = null;
let pendingScroll = null;

// First workspace visit: load the last-used survey (unless the project picker already chose one),
// fill the survey switcher and re-render on survey changes from then on.
function ensureSurvey() {
  surveyReady ??= (survey() ? Promise.resolve(survey()) : initialSurvey()).then((s) => {
    refreshSurveys();
    onChange(() => { if (WORKSPACE.has(parse().parts[0])) route(); });
    return s;
  });
  return surveyReady;
}

function setMode(mode) {
  document.body.dataset.mode = mode;
  renderUser();
}

async function route() {
  hideTip();
  cleanup?.();
  cleanup = null;
  const { parts, params } = parse();
  const name = parts[0] || "";
  const u = user();

  if (WORKSPACE.has(name) || name === "projects") {
    if (!u) { navigate(`#/signin?next=${encodeURIComponent(location.hash)}`); return; }
  }
  document.querySelectorAll(".nav a").forEach((a) => {
    if (a.dataset.route === name) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });

  if (name === "map") {
    setMode("app");
    screen.hidden = true;
    mapScreen.hidden = false;
    if (!survey()) document.getElementById("map-loading").hidden = false;
    const s = await ensureSurvey().catch(() => null);
    if (!s || parse().parts[0] !== "map") return;
    showMap(s, params);
    document.title = `Map · ${s.summary.project} · FarmWings`;
    return;
  }
  hideMap();
  mapScreen.hidden = true;
  screen.hidden = false;
  screen.innerHTML = "";
  window.scrollTo(0, 0);

  if (!name) {
    setMode("public");
    renderLanding(screen);
    document.title = "FarmWings · Drone mapping & plant intelligence";
    if (pendingScroll) { document.getElementById(pendingScroll)?.scrollIntoView({ behavior: "smooth" }); pendingScroll = null; }
    return;
  }
  if (name === "signin") {
    setMode("auth");
    if (u && params.get("demo") !== "1") { navigate(params.get("next") || "#/projects"); return; }
    renderSignin(screen, params, (next) => { renderUser(); navigate(next); });
    document.title = "Sign in · FarmWings";
    return;
  }
  if (name === "projects") {
    setMode("projects");
    cleanup = renderProjects(screen);
    document.title = "Projects · FarmWings";
    return;
  }
  if (name === "method") { navigate("#/overview"); return; }   // archived screen
  if (!WORKSPACE.has(name)) { navigate("#/"); return; }

  setMode("app");
  if (!survey()) screen.innerHTML = `<p class="muted">Loading project…</p>`;
  let s;
  try {
    s = await ensureSurvey();
  } catch (e) {
    screen.innerHTML = `<div class="card"><h3>Could not load the project</h3><p class="muted">${esc(e.message)}</p></div>`;
    return;
  }
  if (parse().parts[0] !== name) return;   // navigated away while loading
  s = survey() || s;
  screen.innerHTML = "";
  if (name === "plants" && parts[1]) renderPlant(screen, s, decodeURIComponent(parts[1]));
  else if (name === "plants") renderPlants(screen, s, params);
  else if (name === "insights") renderInsights(screen, s, params);
  else if (name === "analyze") renderAnalyze(screen);
  else renderOverview(screen, s);
  document.title = `${name === "plants" && parts[1] ? decodeURIComponent(parts[1]) : TITLES[name]} · ${s.summary.project} · FarmWings`;
}

// ---- header: signed-in user menu
const userBox = document.getElementById("user-box");
function renderUser() {
  const u = user();
  const mode = document.body.dataset.mode;
  if (!u) {
    userBox.innerHTML = mode === "public" ? `<a class="btn primary" href="#/signin">Sign in</a>` : "";
    return;
  }
  userBox.innerHTML = `
    ${mode === "public" ? `<a class="btn primary" href="#/projects">Open projects</a>` : ""}
    <div class="user-menu">
      <button class="avatar" aria-haspopup="true" aria-expanded="false" title="${esc(u.name)}">${u.picture ? `<img src="${esc(u.picture)}" alt="" referrerpolicy="no-referrer" />` : esc(initials(u))}</button>
      <div class="menu" hidden>
        <div class="who"><b>${esc(u.name)}</b>${u.email ? `<span>${esc(u.email)}</span>` : ""}${u.kind === "demo" ? `<span class="badge">Demo access</span>` : ""}</div>
        <a href="#/projects">Projects</a>
        <a href="#/">About FarmWings</a>
        <button data-signout>Sign out</button>
      </div>
    </div>`;
  const btn = userBox.querySelector(".avatar");
  const menu = userBox.querySelector(".menu");
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    menu.hidden = !menu.hidden;
    btn.setAttribute("aria-expanded", String(!menu.hidden));
  });
  menu.addEventListener("click", (e) => {
    menu.hidden = true;
    if (e.target.closest("[data-signout]")) { signOut(); navigate("#/"); renderUser(); }
  });
}
document.addEventListener("click", () => { const m = userBox.querySelector(".menu"); if (m) m.hidden = true; });

// ---- landing section links in the header
document.querySelectorAll("[data-scroll]").forEach((b) => b.addEventListener("click", () => {
  const id = b.dataset.scroll;
  if (parse().parts.length) { pendingScroll = id; navigate("#/"); return; }
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth" });
}));

// ---- the map screen fills the viewport under the header, whatever its height
new ResizeObserver(() => document.documentElement.style.setProperty("--bar-h", `${header.offsetHeight}px`)).observe(header);

// ---- survey switcher: the bundled surveys + finished surveys on the compute server
const select = document.getElementById("survey-select");
async function refreshSurveys() {
  const opts = BUNDLED_SURVEYS.map((b) => ({ value: b.id, label: b.name, bundled: b }));
  try {
    const r = await api("/api/jobs", { signal: AbortSignal.timeout(2500) });
    if (r.ok) {
      for (const j of await r.json()) if (j.status === "done") opts.push({ value: `job:${j.id}`, label: j.name, job: j });
    }
  } catch { /* no compute server running: only the bundled surveys */ }
  const cur = survey()?.id;
  select.innerHTML = opts.map((o) => `<option value="${esc(o.value)}" ${o.value === cur ? "selected" : ""}>${esc(o.label)}</option>`).join("");
  select.onchange = async () => {
    const o = opts.find((x) => x.value === select.value);
    await useSurvey(o.bundled || jobSurvey(serverUrl(), o.job));
  };
}
onChange((s) => { if ([...select.options].some((o) => o.value === s.id)) select.value = s.id; else refreshSurveys(); });
export { refreshSurveys };
window.farmwings = { refreshSurveys, survey };

window.addEventListener("hashchange", route);
route();
