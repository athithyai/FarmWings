// Import a survey: connect a FarmWings compute node (this PC or a cloud GPU), upload RGB + NDVI,
// choose operations, run the full pipeline, then open the results in every screen.
import { api, authSession, authToken, jobSurvey, serverUrl, setAuth, setServerUrl, useSurvey } from "../store.js";

// Google Identity Services (loaded only when a compute node asks for sign-in)
function loadGis() {
  if (window.google?.accounts?.id) return Promise.resolve();
  return new Promise((res, rej) => {
    const sc = document.createElement("script");
    sc.src = "https://accounts.google.com/gsi/client";
    sc.async = true;
    sc.onload = res;
    sc.onerror = () => rej(new Error("Google sign-in could not load"));
    document.head.appendChild(sc);
  });
}
import { esc, fmt, navigate } from "../ui.js";

const STAGE_LABELS = [
  ["inspect", "Read files"], ["align", "Align RGB + NDVI"], ["lines", "Find drip lines"], ["detect", "Detect plants"],
  ["identify", "Identify plants"], ["health", "Assess health"], ["export", "Build results"], ["tiles", "Map imagery"], ["media", "Plant images"],
];

export function renderAnalyze(el) {
  const files = { rgb: null, ndvi: null };
  el.innerHTML = `
    <div class="page-head"><div><div class="eyebrow">Import</div><h1>Analyze a new survey</h1>
      <p>Upload a drone RGB orthomosaic and an NDVI raster. A FarmWings compute node runs the same models as the Pilot and the results open here: map, plants, insights.</p></div></div>

    <div class="grid g2">
      <div class="card">
        <h3><span class="num" style="color:var(--sand)">1</span> Connect compute</h3>
        <p class="sub">The node runs on a machine with an NVIDIA GPU: this PC or a cloud GPU.</p>
        <div class="filters" style="margin-bottom:8px">
          <label class="field" style="flex:1">Compute address<input id="srv" value="${esc(serverUrl())}" /></label>
          <button class="btn" id="connect">Connect</button>
        </div>
        <div id="srv-status" class="status-light">Not connected</div>
        <div id="auth" style="margin-top:12px"></div>
        <details style="margin-top:12px"><summary class="small" style="cursor:pointer">Start a compute node</summary>
          <p class="small muted">On this PC (from the FarmWings folder):</p>
          <pre class="cmd">.venv\\Scripts\\python.exe server/app.py</pre>
          <p class="small muted">On a cloud GPU (Linux, CUDA): clone the repository, <code>pip install -r requirements.txt</code>, then</p>
          <pre class="cmd">python server/app.py --host 0.0.0.0 --port 8765</pre>
          <p class="small muted">and put the node's HTTPS address above (e.g. behind a reverse proxy or tunnel).</p>
        </details>
      </div>

      <div class="card">
        <h3><span class="num" style="color:var(--sand)">2</span> Add the dataset</h3>
        <p class="sub">GeoTIFFs in a metric CRS (e.g. UTM). RGB 3–4 bands; NDVI 1 band.</p>
        <div class="grid g2" style="gap:10px">
          <label class="drop" id="drop-rgb"><b>RGB orthomosaic</b><span class="fn">Choose or drop a .tif</span><input type="file" accept=".tif,.tiff" hidden /></label>
          <label class="drop" id="drop-ndvi"><b>NDVI raster</b><span class="fn">Choose or drop a .tif</span><input type="file" accept=".tif,.tiff" hidden /></label>
        </div>
        <p class="small muted" style="margin:10px 0 0">No data at hand? Try the sample survey (40 m × 40 m of the Pilot block):
          <a href="sample/sample_rgb.tif" download>sample_rgb.tif</a> (8.5 MB) and <a href="sample/sample_ndvi.tif" download>sample_ndvi.tif</a> (9 MB).
          Its processed result is already in the survey switcher.</p>
        <div class="filters" style="margin:12px 0 0">
          <label class="field" style="flex:1">Survey name<input id="name" placeholder="e.g. Block 198, Feb 2026" /></label>
          <label class="field" style="flex:1">Planted species<input id="species" value="Rhanterium epapposum" /></label>
        </div>
      </div>
    </div>

    <div class="grid g2 section" style="margin-top:16px">
      <div class="card">
        <h3><span class="num" style="color:var(--sand)">3</span> Choose operations</h3>
        <label class="layer"><input type="checkbox" checked disabled /><span>Plant detection<small>Drip lines, planting spots, SAM 2.1 outlines, empty spots (always on)</small></span></label>
        <label class="layer"><input type="checkbox" id="op-identify" checked /><span>Plant identification<small>DINOv3-SAT crown model: planted stock vs other vegetation</small></span></label>
        <label class="layer"><input type="checkbox" id="op-health" checked /><span>Plant health<small>Green canopy + unsupervised condition groups</small></span></label>
        <p class="small muted" style="margin-bottom:0">Estimated time on one L4 GPU: ~30 min per 2.7 ha block.</p>
      </div>
      <div class="card">
        <h3><span class="num" style="color:var(--sand)">4</span> Run</h3>
        <p class="sub" id="run-hint">Connect compute and add both files to start.</p>
        <button class="btn primary" id="run" disabled>Upload and run</button>
        <div id="upload" style="margin-top:12px"></div>
      </div>
    </div>

    <div class="section card"><h3>Surveys on this compute node</h3><div id="jobs" class="empty">Connect to see surveys.</div></div>
`;

  let connected = false;
  let authRequired = false;
  const status = el.querySelector("#srv-status");
  const authBox = el.querySelector("#auth");

  async function renderAuth(info) {
    authRequired = !!info?.required;
    if (!authRequired) { authBox.innerHTML = ""; return; }
    const a = authSession();
    if (a) {
      authBox.innerHTML = `<div class="chip">${a.picture ? `<img src="${esc(a.picture)}" alt="" width="18" height="18" style="border-radius:50%">` : ""}Signed in as ${esc(a.email)}</div>
        <button class="btn" id="signout" style="margin-left:8px">Sign out</button>`;
      authBox.querySelector("#signout").onclick = async () => {
        await api("/api/auth/logout", { method: "POST" }).catch(() => {});
        setAuth(null);
        renderAuth(info);
        refreshJobs();
        update();
      };
      return;
    }
    authBox.innerHTML = `<p class="small" style="margin:0 0 8px">This compute node requires sign-in.</p><div id="gbtn"></div><p class="small" id="auth-err" style="color:var(--bad)"></p>`;
    try {
      await loadGis();
      window.google.accounts.id.initialize({
        client_id: info.client_id,
        callback: async ({ credential }) => {
          const r = await fetch(`${serverUrl()}/api/auth/google`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ credential }) });
          const j = await r.json().catch(() => ({}));
          if (!r.ok) { authBox.querySelector("#auth-err").textContent = j.error || "Sign-in failed"; return; }
          setAuth(j);
          renderAuth(info);
          refreshJobs();
          window.farmwings?.refreshSurveys?.();
          update();
        },
      });
      window.google.accounts.id.renderButton(authBox.querySelector("#gbtn"), { theme: "outline", size: "large", text: "signin_with", shape: "pill" });
    } catch (e) {
      authBox.querySelector("#auth-err").textContent = e.message;
    }
  }
  async function connect() {
    const url = el.querySelector("#srv").value.trim().replace(/\/+$/, "");
    setServerUrl(url);
    status.className = "status-light";
    status.textContent = "Connecting…";
    try {
      const r = await fetch(`${url}/api/status`, { signal: AbortSignal.timeout(4000) });
      const j = await r.json();
      connected = true;
      await renderAuth(j.auth);
      status.className = "status-light ok";
      status.textContent = `Connected · ${j.gpu || "no GPU detected"} · ${j.running ? "1 survey running" : "idle"}${j.queued ? ` · ${j.queued} queued` : ""}`;
      refreshJobs();
      window.farmwings?.refreshSurveys?.();
    } catch {
      connected = false;
      status.className = "status-light bad";
      status.textContent = "No compute node at this address. Start one (see below) and connect again.";
    }
    update();
  }

  function update() {
    const signedIn = !authRequired || !!authToken();
    const ok = connected && signedIn && files.rgb && files.ndvi;
    el.querySelector("#run").disabled = !ok;
    el.querySelector("#run-hint").textContent = ok ? `${files.rgb.name} + ${files.ndvi.name} · ${fmt.n((files.rgb.size + files.ndvi.size) / 1e9, 2)} GB` :
      !connected ? "Connect compute to start." : !signedIn ? "Sign in to start." : "Add both files to start.";
  }

  for (const kind of ["rgb", "ndvi"]) {
    const drop = el.querySelector(`#drop-${kind}`);
    const input = drop.querySelector("input");
    const set = (f) => {
      if (!f) return;
      files[kind] = f;
      drop.classList.add("has");
      drop.querySelector(".fn").textContent = `${f.name} · ${fmt.n(f.size / 1e6, 0)} MB`;
      update();
    };
    input.addEventListener("change", () => set(input.files[0]));
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("has"); });
    drop.addEventListener("dragleave", () => { if (!files[kind]) drop.classList.remove("has"); });
    drop.addEventListener("drop", (e) => { e.preventDefault(); set(e.dataTransfer.files[0]); });
  }

  el.querySelector("#connect").addEventListener("click", connect);
  el.querySelector("#run").addEventListener("click", () => {
    const fd = new FormData();
    fd.append("rgb", files.rgb);
    fd.append("ndvi", files.ndvi);
    fd.append("name", el.querySelector("#name").value || files.rgb.name.replace(/\.tiff?$/i, ""));
    fd.append("species", el.querySelector("#species").value);
    fd.append("operations", ["detect", el.querySelector("#op-identify").checked && "identify", el.querySelector("#op-health").checked && "health"].filter(Boolean).join(","));
    const up = el.querySelector("#upload");
    up.innerHTML = `<div class="small muted" id="up-txt">Uploading…</div><div class="progress"><span id="up-bar" style="width:0%"></span></div>`;
    el.querySelector("#run").disabled = true;
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${serverUrl()}/api/jobs`);
    if (authToken()) xhr.setRequestHeader("Authorization", `Bearer ${authToken()}`);
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      el.querySelector("#up-bar").style.width = `${(e.loaded / e.total) * 100}%`;
      el.querySelector("#up-txt").textContent = `Uploading ${fmt.n(e.loaded / 1e6, 0)} of ${fmt.n(e.total / 1e6, 0)} MB`;
    };
    xhr.onload = () => {
      let body = {};
      try { body = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
      if (xhr.status >= 300) {
        up.innerHTML = `<p class="note">The node refused the files: ${esc(body.error || xhr.statusText)}</p>`;
        update();
        return;
      }
      up.innerHTML = `<p class="small">Uploaded. “${esc(body.name)}” is queued; progress is below.</p>`;
      refreshJobs();
    };
    xhr.onerror = () => { up.innerHTML = `<p class="note">Upload failed: the compute node did not respond.</p>`; update(); };
    xhr.send(fd);
  });

  let timer = null;
  async function refreshJobs() {
    clearTimeout(timer);
    if (!document.body.contains(el.querySelector("#jobs"))) return;   // left the screen
    let jobs = [];
    const box0 = el.querySelector("#jobs");
    try {
      const r = await api("/api/jobs");
      if (r.status === 401) { box0.className = "empty"; box0.textContent = "Sign in to see surveys on this compute node."; return; }
      jobs = await r.json();
    } catch { return; }
    const box = el.querySelector("#jobs");
    box.className = "";
    box.innerHTML = jobs.length ? jobs.map((j) => {
      const i = STAGE_LABELS.findIndex(([k]) => k === j.stage);
      const label = j.status === "running" ? `${STAGE_LABELS[i]?.[1] || "Starting"} · ${Math.round((j.progress || 0) * 100)}%` :
        j.status === "queued" ? `Queued${j.queue_position ? ` (#${j.queue_position})` : ""}` : j.status === "done" ? "Ready" : j.status === "failed" ? "Failed" : j.status;
      return `<div class="job">
        <div><div class="name">${esc(j.name)}</div><div class="small muted">${esc(j.files?.rgb || "")} · ${fmt.date(j.created)} · ${(j.operations || []).join(", ")}</div></div>
        <div>${j.status === "done" ? `<button class="btn primary" data-open="${esc(j.id)}">Open results</button>` : `<span class="status-light ${j.status === "failed" ? "bad" : "ok"}">${esc(label)}</span>`}</div>
        ${j.status === "running" || j.status === "queued" ? `<div class="progress"><span style="width:${(j.progress || 0) * 100}%"></span></div>` : ""}
        ${j.status === "done" && j.stats ? `<div class="small muted" style="grid-column:1/-1">${fmt.int(j.stats.planted_positions)} plants located · ${fmt.int(j.stats.green_canopy_planted)} with green canopy · ${fmt.int(j.stats.inferred_missing_positions)} empty spots</div>` : ""}
        ${j.status === "failed" ? `<pre class="cmd" style="grid-column:1/-1;white-space:pre-wrap">${esc(j.error || "")}</pre>` : ""}
      </div>`;
    }).join("") : `<p class="empty">No surveys yet. Upload one above.</p>`;
    box.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", async () => {
      const job = jobs.find((x) => x.id === b.dataset.open);
      b.textContent = "Loading…";
      await useSurvey(jobSurvey(serverUrl(), job));
      window.farmwings?.refreshSurveys?.();
      navigate("#/overview");
    }));
    if (jobs.some((j) => j.status === "running" || j.status === "queued")) timer = setTimeout(refreshJobs, 3000);
  }
  connect();
}
