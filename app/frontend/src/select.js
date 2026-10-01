// A selection is one group of plants the user picked: a health group, canopy status, planted
// stock vs other vegetation, a drip line, the empty spots, or "needs attention". The map, Insights
// and Plants understand the same selection, carried in the URL as ?sel=<kind>:<value>.
import { countBy, mean } from "./ui.js";

const PLANTED = (p) => p.position_type === "Planting line";

export function parseSel(params) {
  const raw = params?.get("sel");
  if (!raw) return null;
  const i = raw.indexOf(":");
  return i < 0 ? { kind: raw, value: "" } : { kind: raw.slice(0, i), value: raw.slice(i + 1) };
}
export const selQuery = (sel) => (sel ? `sel=${encodeURIComponent(`${sel.kind}:${sel.value}`)}` : "");
export const sameSel = (a, b) => !!a && !!b && a.kind === b.kind && a.value === b.value;

export function selLabel(s, sel) {
  if (!sel) return "All planted saplings";
  if (sel.kind === "canopy") return sel.value === "green" ? "Green canopy" : "No green canopy";
  if (sel.kind === "line") return `Drip line ${sel.value}`;
  if (sel.kind === "gaps") return "Empty planting spots";
  if (sel.kind === "attention") return "Needs a field visit";
  return sel.value;
}

export function selColor(s, sel) {
  if (!sel) return "#2f8a3a";
  if (sel.kind === "health") return s.healthClasses.find((c) => c.key === sel.value)?.color || "#888";
  if (sel.kind === "ident") return s.idClasses.find((c) => c.key === sel.value)?.color || "#888";
  if (sel.kind === "canopy") return sel.value === "green" ? "#4caf50" : "#e06a5a";
  if (sel.kind === "gaps" || sel.kind === "attention") return "#ff4f7b";
  return "#2f8a3a";
}

/** Does planted plant p belong to the selection? (gaps are not plants) */
export function matches(sel, p) {
  if (!sel) return PLANTED(p);
  if (!PLANTED(p)) return false;
  if (sel.kind === "health") return p.health_class === sel.value;
  if (sel.kind === "ident") return p.plant_class === sel.value;
  if (sel.kind === "canopy") return sel.value === "green" ? !!p.living_canopy : !p.living_canopy;
  if (sel.kind === "line") return p.line_id === sel.value;
  if (sel.kind === "attention") return !p.living_canopy;
  return false;
}
const gapIn = (sel, g) => !!sel && (sel.kind === "gaps" || sel.kind === "attention" || (sel.kind === "line" && g.properties.line_id === sel.value));

/** MapLibre expression matching the selection on plant features (planted filter applied by the layer). */
export function mapExpr(sel) {
  if (!sel) return true;
  if (sel.kind === "health") return ["==", ["get", "health_class"], sel.value];
  if (sel.kind === "ident") return ["==", ["get", "plant_class"], sel.value];
  if (sel.kind === "canopy") return ["==", ["get", "living_canopy"], sel.value === "green"];
  if (sel.kind === "line") return ["==", ["get", "line_id"], sel.value];
  if (sel.kind === "attention") return ["!=", ["get", "living_canopy"], true];
  return false;   // gaps: no plant matches
}
export function gapExpr(sel) {
  if (!sel || sel.kind === "gaps" || sel.kind === "attention") return true;
  if (sel.kind === "line") return ["==", ["get", "line_id"], sel.value];
  return false;
}

export function selPlants(s, sel) { return s.list.filter((p) => matches(sel, p)); }
export function selGaps(s, sel) { return s.gaps.features.filter((g) => (sel ? gapIn(sel, g) : true)); }

/** Numbers for a selection: counts, shares, means, health mix and the lines where it concentrates. */
export function selStats(s, sel) {
  const planted = s.list.filter(PLANTED);
  const plants = selPlants(s, sel);
  const gaps = selGaps(s, sel);
  const n = plants.length;
  const byLine = countBy(plants, "line_id");
  for (const g of gaps) byLine[g.properties.line_id] = (byLine[g.properties.line_id] || 0) + 1;
  const linesTotal = countBy(planted, "line_id");
  const lines = Object.entries(byLine).filter(([k]) => k && k !== "undefined")
    .map(([line_id, k]) => ({ line_id, n: k, share: k / Math.max(1, linesTotal[line_id] || k) }))
    .sort((a, b) => b.n - a.n || a.line_id.localeCompare(b.line_id));
  return {
    n, gaps: gaps.length, total: planted.length,
    share: n / Math.max(1, planted.length),
    green: plants.filter((p) => p.living_canopy).length,
    meanNdvi: mean(plants, "mean_ndvi"),
    meanCanopyCm2: (mean(plants.filter((p) => p.living_canopy), "canopy_area_m2") ?? 0) * 1e4,
    health: countBy(plants, "health_class"),
    lines,
    plants,
  };
}

/** Plants page link with the matching filters. */
export function plantsLink(sel) {
  if (!sel) return "#/plants";
  const q = { health: "health", ident: "cls", line: "line" }[sel.kind];
  if (q) return `#/plants?${q}=${encodeURIComponent(sel.value)}`;
  if (sel.kind === "canopy") return `#/plants?canopy=${sel.value}`;
  if (sel.kind === "attention") return "#/plants?canopy=none";
  return "#/plants";
}
