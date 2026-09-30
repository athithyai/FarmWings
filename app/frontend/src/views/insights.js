import { barChart, histogram, showTip, hideTip } from "../charts.js";
import { countBy, esc, fmt, navigate } from "../ui.js";

// diverging colour for a 0-1 health score (red - grey - green), same ends as the health groups
function scoreColor(v) {
  const stops = [[0.2, [201, 58, 58]], [0.5, [154, 153, 148]], [0.8, [18, 128, 90]]];
  const t = Math.max(0.2, Math.min(0.8, v ?? 0.5));
  const [a, b] = t <= 0.5 ? [stops[0], stops[1]] : [stops[1], stops[2]];
  const f = (t - a[0]) / (b[0] - a[0]);
  const c = a[1].map((x, i) => Math.round(x + (b[1][i] - x) * f));
  return `rgb(${c.join(",")})`;
}

export function renderInsights(el, s) {
  const planted = s.list.filter((p) => p.position_type === "Planting line");
  const lines = s.lineAgg;
  const worst = [...lines].filter((l) => l.n >= 10).sort((a, b) => a.score - b.score).slice(0, 8);
  const noGreen = planted.filter((p) => !p.living_canopy).length;
  el.innerHTML = `
    <div class="page-head"><div><div class="eyebrow">Insights</div><h1>Where to act</h1>
      <p>Plant condition across the block, drip line by drip line. Click a line to see its plants.</p></div></div>

    <div class="card">
      <h3>Condition by drip line</h3>
      <p class="sub">Each bar is one drip line (${lines.length}); height = plants on it, colour = average condition</p>
      <div id="strip"></div>
      <div class="legend-row" style="margin-top:8px"><span><span class="dot" style="background:${scoreColor(0.2)}"></span> poorer</span>
        <span><span class="dot" style="background:${scoreColor(0.5)}"></span> average</span><span><span class="dot" style="background:${scoreColor(0.8)}"></span> better</span></div>
    </div>

    <div class="grid g2 section">
      <div class="card"><h3>Lines needing attention</h3>
        <table class="mini"><thead><tr><th>Line</th><th class="n">Plants</th><th class="n">No green canopy</th><th class="n">Poor or worse</th></tr></thead><tbody>
        ${worst.map((l) => {
          const pl = planted.filter((p) => p.line_id === l.line_id);
          return `<tr><td><a href="#/plants?line=${l.line_id}">${l.line_id}</a></td><td class="n">${l.n}</td><td class="n">${pl.filter((p) => !p.living_canopy).length}</td><td class="n">${fmt.pct(l.poorShare)}</td></tr>`;
        }).join("")}</tbody></table>
      </div>
      <div class="card"><h3>Plant condition</h3><p class="sub">${fmt.int(planted.length)} planted saplings</p><div id="health"></div></div>
    </div>

    <div class="grid g2 section">
      <div class="card"><h3>Green canopy size</h3><p class="sub">${fmt.int(noGreen)} saplings have no green canopy (not shown)</p><div id="canopy"></div>
        <p class="chart-cap">cm² of NDVI-green canopy per sapling</p></div>
      <div class="card"><h3>Canopy status</h3><div id="canopy-status"></div>
        <p class="chart-cap">No green canopy = the planting spot is there but nothing green is visible (dry, dormant or dead).</p></div>
    </div>`;

  // ---- line strip
  const W = 1100;
  const H = 120;
  const maxN = Math.max(...lines.map((l) => l.n), 1);
  const bw = W / Math.max(lines.length, 1);
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H + 18}`);
  svg.style.width = "100%";
  lines.forEach((l, i) => {
    const h = (l.n / maxN) * H;
    const r = document.createElementNS(ns, "rect");
    r.setAttribute("x", i * bw + 0.5);
    r.setAttribute("y", H - h);
    r.setAttribute("width", Math.max(1, bw - 1.5));
    r.setAttribute("height", h);
    r.setAttribute("rx", 2);
    r.setAttribute("fill", scoreColor(l.score));
    r.style.cursor = "pointer";
    r.addEventListener("mousemove", (ev) => showTip(`<b>${l.line_id}</b><br>${l.n} plants · avg score ${fmt.n(l.score)}<br>${fmt.pct(l.poorShare)} poor or worse`, ev));
    r.addEventListener("mouseleave", hideTip);
    r.addEventListener("click", () => navigate(`#/plants?line=${l.line_id}`));
    svg.appendChild(r);
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
  strip.appendChild(svg);

  const hc = countBy(planted, "health_class");
  barChart(el.querySelector("#health"), s.healthClasses.map((c) => ({ label: c.key, value: hc[c.key] || 0, color: c.color })), { labelW: 150 });
  histogram(el.querySelector("#canopy"), planted.filter((p) => p.living_canopy).map((p) => Math.min(p.canopy_area_m2 * 1e4, 2000)),
    { min: 0, max: 2000, bins: 25, color: "#8fd46b", fmt: (v) => fmt.int(v), unit: "saplings" });
  barChart(el.querySelector("#canopy-status"), [
    { label: "Green canopy", value: planted.length - noGreen, color: "#8fd46b" },
    { label: "No green canopy", value: noGreen, color: "#e06a5a" },
    { label: "Empty spot", value: s.gaps.features.length, color: "#ff6f91", note: "expected by the planting rhythm, nothing detected" },
  ], { labelW: 130, total: planted.length + s.gaps.features.length });
  void esc;
}
