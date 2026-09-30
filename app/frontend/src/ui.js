// Small UI helpers shared by the screens.

export const fmt = {
  int: (v) => (v == null || Number.isNaN(v) ? "–" : Math.round(v).toLocaleString("en-US")),
  pct: (v, d = 0) => (v == null || Number.isNaN(v) ? "–" : `${(v * 100).toFixed(d)}%`),
  n: (v, d = 2) => (v == null || Number.isNaN(v) ? "–" : Number(v).toFixed(d)),
  signed: (v, d = 3) => (v == null ? "–" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(d)}`),
  date: (t) => new Date(t * 1000).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" }),
};

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export function colorOf(classes, key) {
  return classes.find((c) => c.key === key)?.color ?? "#888";
}

export function chip(label, color) {
  return `<span class="chip"><span class="dot" style="background:${color}"></span>${esc(label)}</span>`;
}

export function countBy(list, key) {
  const m = {};
  for (const p of list) m[p[key]] = (m[p[key]] || 0) + 1;
  return m;
}

export function mean(list, key) {
  const v = list.map((p) => p[key]).filter((x) => x != null && !Number.isNaN(x));
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
}

export function kv(pairs) {
  return `<dl class="kv">${pairs.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>`;
}

export function meter(v, color = "var(--ink-2)") {
  return `<div class="meter" role="img" aria-label="${Math.round((v ?? 0) * 100)}%"><span style="width:${Math.max(0, Math.min(1, v ?? 0)) * 100}%;background:${color}"></span></div>`;
}

// Point in polygon (ring of [lon, lat])
export function pip(pt, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if ((yi > pt[1]) !== (yj > pt[1]) && pt[0] < ((xj - xi) * (pt[1] - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

export function ringAreaM2(ring) {
  const R = 6378137;
  const lat0 = (ring.reduce((a, c) => a + c[1], 0) / ring.length) * (Math.PI / 180);
  let s = 0;
  for (let i = 0; i < ring.length - 1; i++) {
    const [x1, y1] = ring[i];
    const [x2, y2] = ring[i + 1];
    s += x1 * Math.cos(lat0) * y2 - x2 * Math.cos(lat0) * y1;
  }
  return Math.abs(s / 2) * (Math.PI / 180) ** 2 * R * R;
}

// lon/lat -> local metres around (lon0, lat0)
export function toLocal(lon, lat, lon0, lat0) {
  return [(lon - lon0) * Math.cos((lat0 * Math.PI) / 180) * 111320, (lat - lat0) * 110574];
}

export function navigate(hash) {
  if (location.hash !== hash) location.hash = hash;
}
