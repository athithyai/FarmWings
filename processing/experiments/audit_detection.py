"""Detection accuracy audit (no ground truth exists, so two independent checks).

1. NDVI coverage: vegetation patches (NDVI above local soil, robust z >= 3) that no detected
   object touches, by size class and position (on a visible drip line or not).
2. Visual audit plots: random 8 m x 8 m plots from the ORIGINAL 6 mm RGB next to NDVI, with
   detections outlined, for manual inspection of misses / false detections.

    python processing/experiments/audit_detection.py [--plots 8] [--seed 7]
Writes processing_outputs/experiments/audit/*.png and audit_coverage.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import DEFAULT_INPUT, OUT, find_inputs, out_dir, write_json  # noqa: E402,I001

import cv2  # noqa: E402
import geopandas as gpd  # noqa: E402
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from rasterio.features import rasterize  # noqa: E402
from rasterio.windows import from_bounds  # noqa: E402

from detect_plants import PIXEL_M, _nan_gauss, _robust_z  # noqa: E402
from detect_lines import LineModel, VIS_MIN  # noqa: E402


def coverage(g):
    with rasterio.open(OUT / "aligned" / "ndvi_coreg.tif") as d:
        nd = d.read(1)
        T = d.transform
    nd = np.where(nd < -2, np.nan, nd)
    z = _robust_z(nd - _nan_gauss(nd, 40))
    m = cv2.morphologyEx((np.nan_to_num(z) >= 3).astype(np.uint8), cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    cov = rasterize(((geom, 1) for geom in g.geometry), out_shape=nd.shape, transform=T, fill=0, dtype="uint8")
    cov = cv2.dilate(cov, np.ones((5, 5), np.uint8))
    n, cc, stats, cent = cv2.connectedComponentsWithStats(m, 8)
    area = stats[1:, 4] * PIXEL_M ** 2
    hit = np.bincount(cc[cov > 0].ravel(), minlength=n)[1:] > 0
    lines = LineModel.load(OUT / "lines" / "lines_model.npz")
    R = np.load(OUT / "lines" / "line_evidence_rot.npy").astype(np.float32)
    rows, cols = cent[1:, 1], cent[1:, 0]
    _, dist, _ = lines.nearest(rows, cols)
    near = np.abs(dist) <= 13
    vis = np.full(len(rows), np.nan)
    sel = np.flatnonzero(near & (area >= 0.02))
    vis[sel] = lines.visibility(rows[sel], cols[sel], np.sqrt(area[sel] / np.pi) / PIXEL_M, R)
    on = near & (np.nan_to_num(vis, nan=-1) >= VIS_MIN)
    out = {}
    for lo, hi in [(0.01, 0.02), (0.02, 0.05), (0.05, 0.1), (0.1, 99)]:
        s = (area >= lo) & (area < hi)
        out[f"{lo}-{hi} m2"] = {
            "patches": int(s.sum()), "covered": int((s & hit).sum()),
            "missed_on_line": int((s & ~hit & on).sum()), "missed_between": int((s & ~hit & ~on).sum()),
        }
    return out, cent[1:], area, hit, on


def plots(g, n, seed, missed_pts):
    rgb_path, _ = find_inputs(DEFAULT_INPUT)
    d_out = out_dir("experiments") / "audit"
    d_out.mkdir(exist_ok=True)
    rng = np.random.default_rng(seed)
    fixed = out_dir("experiments") / "audit_plots.json"
    if fixed.exists():                      # same plots every run, so runs can be compared
        import json
        picks = [type("P", (), {"x": x, "y": y}) for x, y in json.loads(fixed.read_text())]
    else:
        planted = g[g.on_planting_line]
        cen = planted.sample(n, random_state=seed).geometry.centroid
        picks = list(cen)
        write_json(fixed, [[p.x, p.y] for p in picks])
    colors = {"Planting line": "#ffd166", "Between lines": "#5fd4e8"}
    with rasterio.open(rgb_path) as src, rasterio.open(OUT / "aligned" / "ndvi_coreg.tif") as nds:
        for i, c in enumerate(picks):
            half = 4.0
            b = (c.x - half, c.y - half, c.x + half, c.y + half)
            w = from_bounds(*b, src.transform)
            img = src.read([1, 2, 3], window=w, out_shape=(3, 1100, 1100))
            ndv = nds.read(1, window=from_bounds(*b, nds.transform), out_shape=(550, 550))
            sub = g.cx[b[0]:b[2], b[1]:b[3]]
            fig, ax = plt.subplots(1, 2, figsize=(22, 11))
            ax[0].imshow(np.moveaxis(img, 0, -1), extent=[b[0], b[2], b[1], b[3]])
            ax[1].imshow(np.where(ndv < -2, np.nan, ndv), cmap="RdYlGn", vmin=-0.05, vmax=0.55, extent=[b[0], b[2], b[1], b[3]])
            for a in ax:
                for pt, grp in sub.groupby("position_type"):
                    grp.boundary.plot(ax=a, color=colors[pt], lw=1.4)
                rec = sub[sub.detection_source == "NDVI vegetation patch"]
                if len(rec):
                    rec.boundary.plot(ax=a, color="#ff4fd8", lw=1.6, linestyle="--")
                m = missed_pts[(missed_pts[:, 0] > b[0]) & (missed_pts[:, 0] < b[2]) & (missed_pts[:, 1] > b[1]) & (missed_pts[:, 1] < b[3])]
                a.scatter(m[:, 0], m[:, 1], s=60, facecolors="none", edgecolors="red", linewidths=1.5)
                a.set_xlim(b[0], b[2]); a.set_ylim(b[1], b[3]); a.set_xticks([]); a.set_yticks([])
            ax[0].set_title(f"Plot {i + 1}: 6 mm RGB, {len(sub)} objects (yellow planted, cyan between, magenta dashed = NDVI recall, red circle = uncovered NDVI patch >= 0.02 m2)", fontsize=10)
            ax[1].set_title("NDVI (co-registered)")
            plt.tight_layout(); plt.savefig(d_out / f"plot_{i + 1}.png", dpi=55); plt.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plots", type=int, default=8)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    g = gpd.read_file(OUT / "detection" / "plants.geojson")
    cov, cent, area, hit, on = coverage(g)
    with rasterio.open(OUT / "aligned" / "ndvi_coreg.tif") as d:
        xs, ys = rasterio.transform.xy(d.transform, cent[:, 1], cent[:, 0])
    missed = np.c_[xs, ys][(~hit) & (area >= 0.02)]
    write_json(out_dir("experiments") / "audit_coverage.json", cov)
    print(cov)
    plots(g, a.plots, a.seed, missed)


if __name__ == "__main__":
    main()
