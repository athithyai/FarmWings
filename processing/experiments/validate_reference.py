"""Validate FarmWings detections against the project's installation record for the block.

Reference (kept local, git-ignored): processing_outputs/reference/installation_<block>.geojson,
one point per installed plant (5,684 for block 4-9/197/RE), and health_<block>.geojson.

Matching: one-to-one, greedy by distance, a detection matches an installation point when its
centroid lies within MATCH_M (planting spacing is 2 m, so 0.6 m cannot pair with a neighbour).
Reports precision / recall / F1 of 'planted position' detections, the positional offset
between the two datasets, and how our health groups relate to the reference health classes.

    python processing/experiments/validate_reference.py [--block 197]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import OUT, out_dir, write_json  # noqa: E402,I001

import geopandas as gpd  # noqa: E402
import numpy as np  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402

MATCH_M = 0.6


def greedy_match(a: np.ndarray, b: np.ndarray, r: float):
    """One-to-one matching of point sets a, b within radius r (closest pairs first)."""
    tree = cKDTree(b)
    pairs = []
    for i, nbrs in enumerate(tree.query_ball_point(a, r)):
        for j in nbrs:
            pairs.append((float(np.hypot(*(a[i] - b[j]))), i, j))
    pairs.sort()
    ua, ub, out = set(), set(), []
    for d, i, j in pairs:
        if i in ua or j in ub:
            continue
        ua.add(i)
        ub.add(j)
        out.append((i, j, d))
    return out


def evaluate(det: gpd.GeoDataFrame, ref: gpd.GeoDataFrame, offset=(0.0, 0.0)):
    a = np.c_[det.centroid_x.values + offset[0], det.centroid_y.values + offset[1]]
    b = np.c_[ref.geometry.x.values, ref.geometry.y.values]
    m = greedy_match(a, b, MATCH_M)
    tp = len(m)
    prec = tp / max(len(a), 1)
    rec = tp / max(len(b), 1)
    d = np.array([x[2] for x in m]) if m else np.array([np.nan])
    dx = np.array([b[j, 0] - a[i, 0] for i, j, _ in m]) if m else np.array([0.0])
    dy = np.array([b[j, 1] - a[i, 1] for i, j, _ in m]) if m else np.array([0.0])
    return m, {"detections": int(len(a)), "reference": int(len(b)), "matched": tp,
               "precision": prec, "recall": rec, "f1": 2 * prec * rec / max(prec + rec, 1e-9),
               "median_distance_m": float(np.nanmedian(d)), "p90_distance_m": float(np.nanpercentile(d, 90)),
               "median_offset_m": [float(np.median(dx)), float(np.median(dy))]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--block", default="197")
    a = ap.parse_args()
    ref_dir = OUT / "reference"
    inst = gpd.read_file(ref_dir / f"installation_{a.block}.geojson")
    det = gpd.read_file(OUT / "detection" / "plants.geojson")
    planted = det[det.on_planting_line].reset_index(drop=True)

    res = {}
    # 1) as is; 2) after removing the median dataset offset (georeferencing difference)
    _, res["planted_vs_installed"] = evaluate(planted, inst)
    off = res["planted_vs_installed"]["median_offset_m"]
    m, res["planted_vs_installed_offset_corrected"] = evaluate(planted, inst, off)
    _, res["all_detections_vs_installed"] = evaluate(det, inst, off)
    ref_detected = inst[inst.Status == "Detected"]
    _, res["planted_vs_reference_detected"] = evaluate(planted, ref_detected, off)

    # where do unmatched detections / reference points sit?
    matched_det = {i for i, _, _ in m}
    matched_ref = {j for _, j, _ in m}
    un_det = planted.drop(index=list(matched_det))
    un_ref = inst.drop(index=list(matched_ref))
    res["unmatched_planted_by_source"] = un_det.detection_source.value_counts().to_dict() if "detection_source" in un_det else {}
    res["unmatched_reference_status"] = un_ref.Status.value_counts().to_dict()

    # detections per reference point: duplicates within the same planting position
    b = np.c_[inst.geometry.x.values, inst.geometry.y.values]
    tree = cKDTree(b)
    dd, jj = tree.query(np.c_[planted.centroid_x.values + off[0], planted.centroid_y.values + off[1]])
    near = dd <= 1.0
    per_ref = np.bincount(jj[near], minlength=len(inst))
    res["reference_points_with_0_1_2plus_detections_within_1m"] = [int((per_ref == 0).sum()), int((per_ref == 1).sum()),
                                                                   int((per_ref >= 2).sum())]

    # health comparison (only if the health stage has run on this detection set)
    hp = OUT / "health" / "plant_health.geojson"
    href = ref_dir / f"health_{a.block}.geojson"
    if hp.exists() and href.exists():
        hcols = [c for c in ["plant_id", "health_class", "health_score", "canopy_area_m2", "living_canopy"]
                 if c in gpd.read_file(hp, rows=1).columns]
        h = gpd.read_file(hp)[hcols]
        if set(h.plant_id) >= set(planted.plant_id):
            hr = gpd.read_file(href).set_index("Unique_ID")
            pl = planted.merge(h, on="plant_id")
            rows = []
            for i, j, _ in m:
                ref_row = hr.loc[inst.Unique_ID.iloc[j]]
                rows.append((pl.health_class.iloc[i], pl.health_score.iloc[i], ref_row.H_Class,
                             ref_row.H_Score, ref_row.NDVI, ref_row.Canopy_Cov,
                             pl["canopy_area_m2"].iloc[i] if "canopy_area_m2" in pl else np.nan,
                             bool(pl["living_canopy"].iloc[i]) if "living_canopy" in pl else None))
            import pandas as pd
            t = pd.DataFrame(rows, columns=["ours", "our_score", "ref", "ref_score", "ref_ndvi", "ref_canopy",
                                            "our_canopy", "our_living"])
            from scipy.stats import spearmanr as _sp
            if t.our_canopy.notna().any():
                both = t.dropna(subset=["ref_canopy"])
                res["canopy_spearman_ours_vs_ref"] = float(_sp(both.our_canopy, both.ref_canopy).statistic)
                res["canopy_median_m2_ours_vs_ref"] = [float(t.our_canopy.median()), float(both.ref_canopy.median())]
                res["our_living_vs_ref_canopy_present"] = pd.crosstab(t.our_living, t.ref_canopy.notna()).to_dict()
            res["health_crosstab"] = pd.crosstab(t.ours, t.ref).to_dict()
            from scipy.stats import spearmanr
            res["health_spearman_our_score_vs_ref_score"] = float(spearmanr(t.our_score, t.ref_score).statistic)
            res["health_spearman_our_score_vs_ref_ndvi"] = float(spearmanr(t.our_score, t.ref_ndvi, nan_policy="omit").statistic)
    write_json(out_dir("experiments") / "validation_reference.json", res)
    for k, v in res.items():
        print(k, v)


if __name__ == "__main__":
    main()
