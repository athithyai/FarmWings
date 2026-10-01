// Insights: every chart and table is connected. Click a health group, a canopy status, the empty
// spots or a drip line, and all the other views re-draw for that selection. The selection lives in
// the URL (?sel=...) and opens the same selection on the map or in the plant list.
import { barChart, histogram, showTip, hideTip } from "../charts.js";
import { countBy, esc, fmt } from "../ui.js";
import { parseSel, plantsLink, sameSel, selColor, selLabel, selQuery, selStats } from "../select.js";

// diverging colour for a 0-1 health score (red - grey - green), same ends as the health groups
function scoreColor(v) {
  const stops = [[0.2, [201, 58, 58]], [0.5, [154, 153, 148]], [0.8, [18, 128, 90]]];
  const t = Math.max(0.2, Math.min(0.8, v ?? 0.5));
  const [a, b] = t <= 0.5 ? [stops[0], stops[1]] : [stops[1], stops[2]];
  const f = (t - a[0]) / (b[0] - a[0]);
  const c = a[1].map((x, i) => Math.round(x + (b[1][i] - x) * f));
  return `rgb(${c.join(",")})`;
}

export function renderInsights(el, s, params) {
  let sel = parseSel(params);
  const planted = s.list.filter((p) => p.position_type === "Planting line");
  const lines = s.lineAgg;
  const gapsByLine = countBy(s.gaps.features.map((g) => g.properties), "line_id");

  el.innerHTML = `
    <div class="page-head"><div><div class="eyebrow">Insights</div><h1>Where to act</h1>
      <p>Everything here is connected: click a condition, a canopy status, the empty spots or a drip line, and every chart follows.</p></div></div>
    <div class="sel-bar" id="sel-bar"></div>
    <div class="card">
      <div class="card-head"><h3 id="strip-title"></h3><span class="sub" id="strip-sub"></span></div>
      <div id="strip"></div>
      <div class="legend-row" id="strip-legend" style="margin-top:8px"></div>
    </div>
    <div class="grid g2 section">
      <div class="card"><h3 id="table-title"></h3><p class="sub" id="table-sub"></p><div id="table"></div></div>
      <div class="card"><h3>Plant condition</h3><p class="sub" id="health-sub"></p><div id="health"></div></div>
    </div>
    <div class="grid g2 section">
      <div class="card"><h3>Green canopy size</h3><p class="sub" id="canopy-sub"></p><div id="canopy"></div>
        <p class="chart-cap">cm² of green canopy per sapling</p></div>
      <div class="card"><h3>Canopy status</h3><p class="sub">Click a bar to select</p><div id="canopy-status"></div>
        <p class="chart-cap">No green canopy: the planting spot is there but nothing green is visible (dry, dormant or dead).</p></div>
    </div>`;

  function pick(next) {
    sel = next && !sameSel(next, sel) ? next : null;
    history.replaceState(null, "", `#/insights${sel ? `?${selQuery(sel)}` : ""}`);
    draw();
  }

  function draw() {
    const st = selStats(s, sel);
    const color = selColor(s, sel);
    const inSel = st.plants;
    const scope = sel?.kind === "line" ? inSel : planted;   // a line selection scopes the distribution charts

    // ---- selection bar
    const bar = el.querySelector("#sel-bar");
    const big = sel?.kind === "gaps" ? st.gaps : st.n + (sel?.kind === "attention" ? st.gaps : 0);
    const chips = [
      { kind: "attention", value: "", label: "Needs a field visit", color: "#ff4f7b", n: (s.summary.stats.no_green_canopy_planted || 0) + s.gaps.features.length },
      ...s.healthClasses.map((c) => ({ kind: "health", value: c.key, label: c.key.replace(" condition", ""), color: c.color, n: countBy(planted, "health_class")[c.key] || 0 })),
      { kind: "canopy", value: "none", label: "No green canopy", color: "#e06a5a", n: planted.filter((p) => !p.living_canopy).length },
      { kind: "gaps", value: "", label: "Empty spots", color: "#ff6f91", n: s.gaps.features.length },
    ];
    bar.innerHTML = `
      <div class="sel-chips">${chips.map((c) => `<button class="sel-chip ${sameSel(c, sel) ? "on" : ""}" data-kind="${c.kind}" data-value="${esc(c.value)}">
        <span class="dot" style="background:${c.color}"></span>${esc(c.label)} <b class="num">${fmt.int(c.n)}</b></button>`).join("")}</div>
      <div class="sel-summary">${sel ? `
        <div><span class="sel-k">Selected</span><span class="sel-v"><span class="dot" style="background:${color}"></span>${esc(selLabel(s, sel))}</span></div>
        <div><span class="sel-k">${sel.kind === "gaps" ? "Empty spots" : sel.kind === "attention" ? "Spots" : "Plants"}</span><span class="sel-v num">${fmt.int(big)}</span></div>
        ${sel.kind === "gaps" ? "" : `<div><span class="sel-k">Share of planted</span><span class="sel-v num">${fmt.pct(st.share, 1)}</span></div>
        <div><span class="sel-k">Mean NDVI</span><span class="sel-v num">${fmt.n(st.meanNdvi)}</span></div>`}
        <div class="sel-actions"><a class="btn primary" href="#/map?${selQuery(sel)}">Show on map</a>
          ${sel.kind === "gaps" ? "" : `<a class="btn" href="${plantsLink(sel)}">List plants</a>`}<button class="btn" id="sel-clear">Clear</button></div>`
        : `<span class="muted">${fmt.int(planted.length)} planted saplings on ${lines.length} drip lines · pick a group above or click any chart</span>`}</div>`;
    bar.querySelectorAll(".sel-chip").forEach((b) => b.addEventListener("click", () => pick({ kind: b.dataset.kind, value: b.dataset.value })));
    bar.querySelector("#sel-clear")?.addEventListener("click", () => pick(null));

    // ---- drip-line strip: average condition, or how many of the selection each line holds
    const perLine = (l) => (sel && sel.kind !== "line" ? (countBy(inSel, "line_id")[l.line_id] || 0) + (sel.kind === "gaps" || sel.kind === "attention" ? gapsByLine[l.line_id] || 0 : 0) : l.n);
    el.querySelector("#strip-title").textContent = sel && sel.kind !== "line" ? `${selLabel(s, sel)} by drip line` : "Condition by drip line";
    el.querySelector("#strip-sub").textContent = sel && sel.kind !== "line" ? "bar height = how many on each line · click a line to select it" : `each bar is one drip line (${lines.length}); height = plants, colour = average condition · click a line`;
    el.querySelector("#strip-legend").innerHTML = sel && sel.kind !== "line" ? `<span><span class="dot" style="background:${color}"></span> ${esc(selLabel(s, sel))}</span>`
      : `<span><span class="dot" style="background:${scoreColor(0.2)}"></span> poorer</span><span><span class="dot" style="background:${scoreColor(0.5)}"></span> average</span><span><span class="dot" style="background:${scoreColor(0.8)}"></span> better</span>`;
    const W = 1100;
    const H = 120;
    const vals = lines.map(perLine);
    const maxN = Math.max(...vals, 1);
    const bw = W / Math.max(lines.length, 1);
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H + 18}`);
    svg.style.width = "100%";
    lines.forEach((l, i) => {
      const h = (vals[i] / maxN) * H;
      const isSel = sel?.kind === "line" && sel.value === l.line_id;
      const r = document.createElementNS(ns, "rect");
      r.setAttribute("x", i * bw + 0.5);
      r.setAttribute("y", H - Math.max(h, vals[i] ? 1.5 : 0));
      r.setAttribute("width", Math.max(1, bw - 1.5));
      r.setAttribute("height", Math.max(h, vals[i] ? 1.5 : 0));
      r.setAttribute("rx", 2);
      r.setAttribute("fill", sel && sel.kind !== "line" ? color : scoreColor(l.score));
      if (sel?.kind === "line" && !isSel) r.setAttribute("opacity", "0.25");
      r.style.cursor = "pointer";
      const hit = document.createElementNS(ns, "rect");
      hit.setAttribute("x", i * bw); hit.setAttribute("y", 0); hit.setAttribute("width", bw); hit.setAttribute("height", H);
      hit.setAttribute("class", "hit");
      hit.style.cursor = "pointer";
      const tipHtml = `<b>${l.line_id}</b><br>${l.n} plants · avg score ${fmt.n(l.score)} · ${fmt.pct(l.poorShare)} poor or worse${sel && sel.kind !== "line" ? `<br><b>${fmt.int(vals[i])}</b> ${esc(selLabel(s, sel).toLowerCase())}` : ""}<br><span class="muted">Click to select this line</span>`;
      hit.addEventListener("mousemove", (ev) => showTip(tipHtml, ev));
      hit.addEventListener("mouseleave", hideTip);
      hit.addEventListener("click", () => { hideTip(); pick({ kind: "line", value: l.line_id }); });
      svg.appendChild(r);
      svg.appendChild(hit);
    });
    [0, lines.length - 1].forEach((i) => {
      const t = document.createElementNS(ns, "text");
      t.setAttribute("x", i === 0 ? 0 : W);
      t.setAttribute("y", H + 14);
      t.setAttribute("text-anchor", i === 0 ? "start" : "end");
      t.setAttribute("class", "axis");
      t.textContent = lines[i]?.line_id || "";
      svg.appendChild(t);
    });
    const strip = el.querySelector("#strip");
    strip.className = "chart";
    strip.innerHTML = "";
    strip.appendChild(svg);

    // ---- table: the lines that hold most of the selection (or the weakest lines)
    const table = el.querySelector("#table");
    if (sel?.kind === "line") {
      const l = lines.find((x) => x.line_id === sel.value);
      const hc = countBy(inSel, "health_class");
      el.querySelector("#table-title").textContent = `Drip line ${sel.value}`;
      el.querySelector("#table-sub").textContent = "Its plants, condition and empty spots";
      table.innerHTML = `<dl class="kv">
        <dt>Planted saplings</dt><dd>${fmt.int(inSel.length)}</dd>
        <dt>Green canopy</dt><dd>${fmt.int(st.green)} · ${fmt.pct(st.green / (inSel.length || 1))}</dd>
        <dt>Empty spots</dt><dd>${fmt.int(gapsByLine[sel.value] || 0)}</dd>
        <dt>Average health score</dt><dd>${fmt.n(l?.score)}</dd>
        <dt>Mean NDVI</dt><dd>${fmt.n(st.meanNdvi)}</dd></dl>
        <div class="health-strip" style="margin-top:12px">${s.healthClasses.map((c) => `<span title="${esc(c.key)}: ${hc[c.key] || 0}" style="width:${((hc[c.key] || 0) / (inSel.length || 1)) * 100}%;background:${c.color}"></span>`).join("")}</div>
        <div class="sel-actions" style="margin-top:12px"><a class="btn primary" href="#/map?${selQuery(sel)}">Show line on map</a><a class="btn" href="${plantsLink(sel)}">List its plants</a></div>`;
    } else {
      const rows = sel
        ? st.lines.slice(0, 8).map((x) => ({ line_id: x.line_id, a: x.n, b: x.share }))
        : [...lines].filter((l) => l.n >= 10).sort((a, b) => a.score - b.score).slice(0, 8)
          .map((l) => ({ line_id: l.line_id, a: planted.filter((p) => p.line_id === l.line_id && !p.living_canopy).length, b: l.poorShare }));
      el.querySelector("#table-title").textContent = sel ? `Where: ${selLabel(s, sel)}` : "Lines needing attention";
      el.querySelector("#table-sub").textContent = sel ? "Drip lines holding the most · click a row" : "Lowest average condition · click a row";
      table.innerHTML = `<table class="mini click"><thead><tr><th>Line</th><th class="n">${sel ? "Count" : "No green canopy"}</th><th class="n">${sel ? "Share of line" : "Poor or worse"}</th><th></th></tr></thead><tbody>
        ${rows.map((r) => `<tr data-line="${esc(r.line_id)}"><td><b>${esc(r.line_id)}</b></td><td class="n">${fmt.int(r.a)}</td><td class="n">${fmt.pct(r.b)}</td>
          <td style="width:30%"><div class="meter" style="margin:0"><span style="width:${Math.min(1, r.b) * 100}%;background:${sel ? color : scoreColor(1 - r.b)}"></span></div></td></tr>`).join("")}</tbody></table>`;
      table.querySelectorAll("tr[data-line]").forEach((tr) => tr.addEventListener("click", () => pick({ kind: "line", value: tr.dataset.line })));
    }

    // ---- condition bars (clickable), scoped to a selected line
    const hc = countBy(scope, "health_class");
    el.querySelector("#health-sub").textContent = `${fmt.int(scope.length)} planted saplings${sel?.kind === "line" ? ` on ${sel.value}` : ""} · click a bar to select`;
    barChart(el.querySelector("#health"), s.healthClasses.map((c) => ({ key: c.key, label: c.key, value: hc[c.key] || 0, color: c.color })),
      { labelW: 150, onPick: (d) => pick({ kind: "health", value: d.key }), selected: sel?.kind === "health" ? sel.value : undefined });

    // ---- canopy size histogram for the selection
    const canopyVals = (sel ? inSel : planted).filter((p) => p.living_canopy).map((p) => Math.min(p.canopy_area_m2 * 1e4, 2000));
    el.querySelector("#canopy-sub").textContent = `${fmt.int(canopyVals.length)} green saplings${sel ? ` · ${selLabel(s, sel)}` : ""}`;
    if (canopyVals.length) histogram(el.querySelector("#canopy"), canopyVals, { min: 0, max: 2000, bins: 25, color: sel ? color : "#8fd46b", fmt: (v) => fmt.int(v), unit: "saplings" });
    else el.querySelector("#canopy").innerHTML = '<p class="empty">No green saplings in this selection.</p>';

    // ---- canopy status (clickable), scoped to a selected line
    const noGreen = scope.filter((p) => !p.living_canopy).length;
    const gapsN = sel?.kind === "line" ? gapsByLine[sel.value] || 0 : s.gaps.features.length;
    const selKey = sel?.kind === "canopy" ? `canopy:${sel.value}` : sel?.kind === "gaps" ? "gaps:" : undefined;
    barChart(el.querySelector("#canopy-status"), [
      { key: "canopy:green", label: "Green canopy", value: scope.length - noGreen, color: "#8fd46b" },
      { key: "canopy:none", label: "No green canopy", value: noGreen, color: "#e06a5a" },
      { key: "gaps:", label: "Empty spot", value: gapsN, color: "#ff6f91", note: "expected by the planting rhythm, nothing there" },
    ], { labelW: 130, total: scope.length + gapsN, selected: selKey,
      onPick: (d) => { const [kind, value] = d.key.split(":"); pick({ kind, value }); } });
  }
  draw();
}
