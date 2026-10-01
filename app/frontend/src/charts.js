// Minimal SVG charts: horizontal bars (categories) and column histograms.
// Thin marks, 4px rounded data-ends, 2px gaps, labels in ink colours (never the
// series colour), hover tooltip on every mark.

const NS = "http://www.w3.org/2000/svg";
const tip = () => document.getElementById("tooltip");

function el(name, attrs = {}, parent) {
  const e = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}

export function showTip(html, ev) {
  const t = tip();
  t.innerHTML = html;
  t.hidden = false;
  const pad = 14;
  let x = ev.clientX + pad;
  let y = ev.clientY + pad;
  const r = t.getBoundingClientRect();
  if (x + r.width > window.innerWidth - 8) x = ev.clientX - r.width - pad;
  if (y + r.height > window.innerHeight - 8) y = ev.clientY - r.height - pad;
  t.style.left = `${x}px`;
  t.style.top = `${y}px`;
}
export function hideTip() {
  tip().hidden = true;
}

function bindTip(node, html) {
  node.addEventListener("mousemove", (ev) => showTip(html, ev));
  node.addEventListener("mouseleave", hideTip);
}

const fmtInt = (v) => v.toLocaleString("en-US");

/** items: [{label, value, color, note?, key?}] - horizontal bars with direct labels.
 *  onPick(item) makes bars clickable; selected (an item key or label) dims the other bars. */
export function barChart(container, items, { total, width = 320, barH = 16, gap = 8, labelW = 118, onPick, selected } = {}) {
  container.innerHTML = "";
  const wrap = document.createElement("div");
  wrap.className = "chart";
  const sum = total ?? items.reduce((a, b) => a + b.value, 0);
  const max = Math.max(1, ...items.map((d) => d.value));
  const valW = 70;
  const plotW = width - labelW - valW;
  const h = items.length * (barH + gap) - gap + 2;
  const svg = el("svg", { viewBox: `0 0 ${width} ${h}`, role: "img" });
  items.forEach((d, i) => {
    const y = i * (barH + gap);
    const w = Math.max(d.value > 0 ? 3 : 0, (d.value / max) * plotW);
    const g = el("g", {}, svg);
    const key = d.key ?? d.label;
    if (selected != null && selected !== key) g.setAttribute("opacity", "0.28");
    if (onPick) g.style.cursor = "pointer";
    el("text", { x: 0, y: y + barH / 2 + 4, ...(selected === key ? { class: "sel" } : {}) }, g).textContent = d.label;
    // bar anchored at the baseline: square left end, 4px rounded data end
    const x0 = labelW;
    const r = Math.min(4, w / 2);
    el("path", {
      d: `M${x0},${y} h${w - r} a${r},${r} 0 0 1 ${r},${r} v${barH - 2 * r} a${r},${r} 0 0 1 -${r},${r} h-${w - r} z`,
      fill: d.color,
    }, g);
    const pct = sum ? ` · ${((d.value / sum) * 100).toFixed(0)}%` : "";
    el("text", { x: x0 + w + 6, y: y + barH / 2 + 4, class: "v" }, g).textContent = `${fmtInt(d.value)}${pct}`;
    const hit = el("rect", { x: 0, y: y - gap / 2, width, height: barH + gap, class: "hit" }, g);
    bindTip(hit, `<b>${d.label}</b><br>${fmtInt(d.value)} plants${pct}${d.note ? `<br><span class="muted">${d.note}</span>` : ""}${onPick ? '<br><span class="muted">Click to select</span>' : ""}`);
    if (onPick) hit.addEventListener("click", () => { hideTip(); onPick(d); });
  });
  el("line", { x1: labelW, x2: labelW, y1: -2, y2: h, class: "base" }, svg);
  wrap.appendChild(svg);
  container.appendChild(wrap);
}

/** Column histogram of numeric values. */
export function histogram(container, values, { min, max, bins = 24, color = "#3987e5", width = 320, height = 110,
  fmt = (v) => v.toFixed(2), unit = "plants", marker } = {}) {
  container.innerHTML = "";
  const wrap = document.createElement("div");
  wrap.className = "chart";
  const lo = min ?? Math.min(...values);
  const hi = max ?? Math.max(...values);
  const step = (hi - lo) / bins || 1;
  const counts = new Array(bins).fill(0);
  for (const v of values) {
    if (v == null || Number.isNaN(v)) continue;
    const k = Math.min(bins - 1, Math.max(0, Math.floor((v - lo) / step)));
    counts[k] += 1;
  }
  const cmax = Math.max(1, ...counts);
  const padB = 16;
  const plotH = height - padB;
  const colW = width / bins;
  const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, role: "img" });
  [0.5, 1].forEach((f) => el("line", { x1: 0, x2: width, y1: plotH - f * plotH, y2: plotH - f * plotH, class: "grid" }, svg));
  counts.forEach((c, i) => {
    const hh = (c / cmax) * (plotH - 2);
    const x = i * colW + 1;
    const w = Math.max(1, colW - 2); // 2px gap between columns
    const r = Math.min(4, w / 2, hh / 2);
    if (c > 0) {
      el("path", {
        d: `M${x},${plotH} v-${hh - r} a${r},${r} 0 0 1 ${r},-${r} h${w - 2 * r} a${r},${r} 0 0 1 ${r},${r} v${hh - r} z`,
        fill: color,
      }, svg);
    }
    const a = lo + i * step;
    const hit = el("rect", { x: i * colW, y: 0, width: colW, height: plotH, class: "hit" }, svg);
    bindTip(hit, `<b>${fmt(a)} – ${fmt(a + step)}</b><br>${fmtInt(c)} ${unit}`);
  });
  el("line", { x1: 0, x2: width, y1: plotH, y2: plotH, class: "base" }, svg);
  if (marker != null) {
    const mx = ((marker.value - lo) / (hi - lo)) * width;
    el("line", { x1: mx, x2: mx, y1: 0, y2: plotH, stroke: "#f4f4f0", "stroke-width": 1.5, "stroke-dasharray": "3 3" }, svg);
    el("text", { x: Math.min(mx + 4, width - 70), y: 10, class: "axis" }, svg).textContent = marker.label;
  }
  el("text", { x: 0, y: height - 2, class: "axis" }, svg).textContent = fmt(lo);
  el("text", { x: width / 2, y: height - 2, class: "axis", "text-anchor": "middle" }, svg).textContent = fmt((lo + hi) / 2);
  el("text", { x: width, y: height - 2, class: "axis", "text-anchor": "end" }, svg).textContent = fmt(hi);
  wrap.appendChild(svg);
  container.appendChild(wrap);
}
