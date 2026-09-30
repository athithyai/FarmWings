"""Contact sheets for manual inspection where FarmWings and the installation record disagree.

  A  record planting spots with no FarmWings plant within 0.6 m
  B  FarmWings planted plants with no record spot within 0.6 m
  C  record spots the other app marks Not_Detected (what FarmWings says there)

Each tile: 1.6 m window from the ORIGINAL 6 mm RGB (left) and NDVI (right); record spot = white
cross, FarmWings planted outline = yellow, other FarmWings objects = cyan. Tiles are numbered so
a reviewer can note a verdict per tile. Writes processing_outputs/experiments/disagree_<A|B|C>.png
and disagreements.csv
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import DEFAULT_INPUT, OUT, find_inputs, out_dir  # noqa: E402,I001

import geopandas as gpd  # noqa: E402
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio  # noqa: E402
from rasterio.windows import from_bounds  # noqa: E402

from validate_reference import greedy_match  # noqa: E402

HALF = 0.8


def sheet(points, det, name, title, n=30, seed=0):
    rgb_path, _ = find_inputs(DEFAULT_INPUT)
    rng = np.random.default_rng(seed)
    pts = points if len(points) <= n else points.iloc[np.sort(rng.choice(len(points), n, replace=False))]
    cols = 6
    rows = int(np.ceil(len(pts) / cols))
    fig, axes = plt.subplots(rows, cols * 2, figsize=(cols * 2 * 2.1, rows * 2.25))
    axes = np.atleast_2d(axes)
    with rasterio.open(rgb_path) as src, rasterio.open(OUT / "aligned" / "ndvi_coreg.tif") as nds:
        for k in range(rows * cols):
            ax_r, ax_n = axes[k // cols, (k % cols) * 2], axes[k // cols, (k % cols) * 2 + 1]
            ax_r.axis("off"); ax_n.axis("off")
            if k >= len(pts):
                continue
            x, y = pts.x.iloc[k], pts.y.iloc[k]
            b = (x - HALF, y - HALF, x + HALF, y + HALF)
            img = src.read([1, 2, 3], window=from_bounds(*b, src.transform), out_shape=(3, 267, 267))
            nd = nds.read(1, window=from_bounds(*b, nds.transform), out_shape=(68, 68))
            ax_r.imshow(np.moveaxis(img, 0, -1), extent=[b[0], b[2], b[1], b[3]])
            ax_n.imshow(np.where(nd < -2, np.nan, nd), cmap="RdYlGn", vmin=-0.05, vmax=0.55, extent=[b[0], b[2], b[1], b[3]])
            sub = det.cx[b[0]:b[2], b[1]:b[3]]
            for a in (ax_r, ax_n):
                if len(sub):
                    sub[sub.on_planting_line].boundary.plot(ax=a, color="#ffd166", lw=1.2)
                    sub[~sub.on_planting_line].boundary.plot(ax=a, color="#5fd4e8", lw=1.0, linestyle="--")
                if "rx" in pts:
                    a.plot(pts.rx.iloc[k], pts.ry.iloc[k], "+", color="white", ms=14, mew=2)
                a.set_xlim(b[0], b[2]); a.set_ylim(b[1], b[3])
            ax_r.set_title(f"{name}{k + 1}", fontsize=9, loc="left")
    fig.suptitle(title, fontsize=11)
    plt.tight_layout()
    plt.savefig(out_dir("experiments") / f"disagree_{name}.png", dpi=60)
    plt.close()
    return pts


def main():
    inst = gpd.read_file(OUT / "reference" / "installation_197.geojson")
    det = gpd.read_file(OUT / "detection" / "plants.geojson")
    pl = det[det.on_planting_line].reset_index(drop=True)
    a = np.c_[pl.centroid_x, pl.centroid_y]
    b = np.c_[inst.geometry.x, inst.geometry.y]
    m = greedy_match(a, b, 0.6)
    md, mr = {i for i, _, _ in m}, {j for _, j, _ in m}
    A = inst.drop(index=list(mr))
    A = pd.DataFrame({"x": A.geometry.x, "y": A.geometry.y, "rx": A.geometry.x, "ry": A.geometry.y,
                      "id": A.Unique_ID, "their_status": A.Status})
    B = pl.drop(index=list(md))
    B = pd.DataFrame({"x": B.centroid_x, "y": B.centroid_y, "id": B.plant_id, "source": B.detection_source})
    C = inst[inst.Status == "Not_Detected"]
    matched_ref_to_det = {j: i for i, j, _ in m}
    C = pd.DataFrame({"x": C.geometry.x, "y": C.geometry.y, "rx": C.geometry.x, "ry": C.geometry.y, "id": C.Unique_ID,
                      "farmwings": ["plant found" if j in matched_ref_to_det else "no plant" for j in C.index]})
    pa = sheet(A, det, "A", f"A: {len(A)} record spots with no FarmWings plant (sample of 30); white + = record spot")
    pb = sheet(B, det, "B", f"B: {len(B)} FarmWings planted plants away from record spots (sample of 30)")
    pc = sheet(C, det, "C", f"C: {len(C)} spots the other app marks Not_Detected; FarmWings: {C.farmwings.value_counts().to_dict()}", n=30)
    pd.concat([pa.assign(group="A"), pb.assign(group="B"), pc.assign(group="C")]).to_csv(
        out_dir("experiments") / "disagreements.csv", index=False)
    print({"A": len(A), "B": len(B), "C": len(C), "C_farmwings": C.farmwings.value_counts().to_dict()})


if __name__ == "__main__":
    main()
