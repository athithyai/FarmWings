"""Stage 3 - Plant Identification Model  ("what plant is this?").

Separate from detection and from health. Input per detected plant:
  * 1.2 m x 1.2 m RGB crop from the original 6 mm orthomosaic, crown-focused (drip line
    inpainted, pixels outside the crown replaced by sand colour - see crown_crops) ->
    frozen DINOv3 ViT-L/16 SAT-493M embedding (CLS + mean patch token); backbone chosen
    by spatial cross-validation, see experiments/test_identification.py and
    docs/model-evaluation.md
  * NDVI distribution inside the crown (mean, median, p10, p90, std)
  * RGB colour indices inside the crown (ExG, VARI, GLI, R/G, green fraction, brightness)
  * crown geometry (area, equivalent diameter, solidity, elongation)
-> standardised features -> L2-regularised logistic regression.

Labels (weak supervision, no manual annotation available):
  the planting design gives an appearance-independent label source. Objects centred on a
  detected drip line with a clear plant/pit core are planted stock ("Palm (planted)" - the
  species was supplied by the project, the imagery cannot confirm species at this crown
  size); vegetation between lines is spontaneous ("Other vegetation"). Position is NOT a
  model input, so the classifier has to recognise the planted stock by appearance.

Output classes: Palm (planted) / Other vegetation / Unclassified (max probability < 0.7).
Outputs (processing_outputs/identification/): plant_identification.geojson,
identification_summary.json, identification_examples.png
"""
from __future__ import annotations

import argparse

from common import DEFAULT_INPUT, OUT, PROCESSING_DATE, find_inputs, out_dir, write_json  # noqa: I001

import numpy as np

PALM, OTHER, UNCLASSIFIED = "Palm (planted)", "Other vegetation", "Unclassified"
MIN_CONFIDENCE = 0.7
IDENT_MODEL = "Frozen {backbone} crown-crop embedding + NDVI/RGB/geometry features -> logistic regression"
IDENT_VERSION = "0.1.0"

BACKBONES = {
    "dinov2-base": "facebook/dinov2-base",
    "dinov3-vitl16-sat493m": "facebook/dinov3-vitl16-pretrain-sat493m",
    "clip-vit-l14": "openai/clip-vit-large-patch14",
}
DEFAULT_BACKBONE = "dinov3-vitl16-sat493m"

TABULAR = ["mean_ndvi", "median_ndvi", "p10_ndvi", "p90_ndvi", "std_ndvi",
           "exg", "vari", "gli", "rg_ratio", "green_fraction", "brightness",
           "area_m2", "equiv_diameter_m", "solidity", "elongation"]


def cache_dir():
    d = OUT / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_crops(gdf, input_dir):
    from features import extract_crops
    path = cache_dir() / "crops_1p2m_224.npy"
    ids = cache_dir() / "crops_ids.npy"
    if path.exists() and ids.exists() and list(np.load(ids, allow_pickle=True)) == list(gdf.plant_id):
        return np.load(path)
    rgb_path, _ = find_inputs(input_dir)
    crops = extract_crops(gdf, rgb_path)
    np.save(path, crops)
    np.save(ids, np.array(gdf.plant_id, dtype=object))
    return crops


def crown_crops(crops: np.ndarray, gdf, size_m=1.2, dilate_px=9) -> np.ndarray:
    """Remove context that encodes position rather than plant appearance.

    1. inpaint the black drip line: thin dark pixels (black top-hat) that survive a
       morphological opening with a 41 px line kernel oriented along the detected drip-line
       direction - only long straight segments are removed, plant leaves are kept
    2. replace everything outside the dilated crown polygon with the median sand colour
    Without this the classifier can learn 'irrigation line through the centre' = planted.
    """
    import cv2
    from common import read_json
    n, S = len(crops), crops.shape[1]
    scale = S / size_m
    out = np.empty_like(crops)
    k_th = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    k_dil = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate_px, dilate_px))
    # Line direction in image coordinates (x right, y down)
    ang = 90.0 - read_json(OUT / "lines" / "lines_summary.json")["angle_deg_rotation_to_vertical"]
    L = 41
    k_line = np.zeros((L, L), np.uint8)
    dx, dy = np.cos(np.radians(ang)) * (L // 2), -np.sin(np.radians(ang)) * (L // 2)
    cv2.line(k_line, (int(round(L // 2 - dx)), int(round(L // 2 - dy))),
             (int(round(L // 2 + dx)), int(round(L // 2 + dy))), 1, 1)
    for i in range(n):
        img = crops[i]
        grey = img.mean(-1).astype(np.float32)
        th = cv2.morphologyEx(grey, cv2.MORPH_CLOSE, k_th) - grey
        line = ((th > 10) & (grey < 130)).astype(np.uint8)
        line = cv2.morphologyEx(line, cv2.MORPH_OPEN, k_line)
        line = cv2.dilate(line, np.ones((5, 5), np.uint8))
        img = cv2.inpaint(img, line, 3, cv2.INPAINT_TELEA) if line.any() else img.copy()
        g = gdf.geometry.iloc[i]
        cx, cy = gdf.centroid_x.iloc[i], gdf.centroid_y.iloc[i]
        xy = np.asarray(g.exterior.coords)
        pts = np.c_[(xy[:, 0] - cx) * scale + S / 2, (cy - xy[:, 1]) * scale + S / 2].astype(np.int32)
        m = np.zeros((S, S), np.uint8)
        cv2.fillPoly(m, [pts], 1)
        m = cv2.dilate(m, k_dil).astype(bool)
        bg = np.median(img[~m], axis=0) if (~m).any() else np.array([180, 160, 130])
        img[~m] = bg.astype(np.uint8)
        out[i] = img
    return out


def load_crown_crops(gdf, crops):
    path = cache_dir() / "crown_crops_1p2m_224.npy"
    if path.exists() and len(np.load(path, mmap_mode="r")) == len(gdf):
        return np.load(path)
    cc = crown_crops(crops, gdf)
    np.save(path, cc)
    return cc


def embed(crops: np.ndarray, backbone: str, batch=64, tag: str = "") -> np.ndarray:
    import torch
    from transformers import AutoImageProcessor, AutoModel, CLIPModel, CLIPProcessor
    cache = cache_dir() / f"emb_{backbone}{tag}.npy"
    if cache.exists() and len(np.load(cache, mmap_mode="r")) == len(crops):
        return np.load(cache)
    repo = BACKBONES[backbone]
    if backbone.startswith("clip"):
        model = CLIPModel.from_pretrained(repo, local_files_only=True).cuda().eval().half()
        proc = CLIPProcessor.from_pretrained(repo, local_files_only=True)
    else:
        model = AutoModel.from_pretrained(repo, local_files_only=True).cuda().eval().half()
        proc = AutoImageProcessor.from_pretrained(repo, local_files_only=True)
    feats = []
    with torch.inference_mode():
        for i in range(0, len(crops), batch):
            imgs = list(crops[i:i + batch])
            if backbone.startswith("clip"):
                x = proc(images=imgs, return_tensors="pt")["pixel_values"].cuda().half()
                f = model.get_image_features(pixel_values=x)
                f = getattr(f, "pooler_output", f)
            else:
                x = proc(images=imgs, return_tensors="pt")["pixel_values"].cuda().half()
                o = model(pixel_values=x)
                cls = o.pooler_output if getattr(o, "pooler_output", None) is not None else o.last_hidden_state[:, 0]
                patch_mean = o.last_hidden_state[:, 1:].mean(1)
                f = torch.cat([cls, patch_mean], 1)
            feats.append(f.float().cpu().numpy())
    feats = np.concatenate(feats)
    np.save(cache, feats)
    del model
    torch.cuda.empty_cache()
    return feats


def tabular_features(gdf):
    import pandas as pd
    from features import ndvi_stats, plant_pixels, rgb_indices
    cache = cache_dir() / "ident_tabular.csv"
    if cache.exists():
        t = pd.read_csv(cache)
        if list(t.plant_id) == list(gdf.plant_id):
            return t
    nds, rgbs = plant_pixels(gdf)
    rows = []
    for pid, v, px in zip(gdf.plant_id, nds, rgbs):
        rows.append({"plant_id": pid, **ndvi_stats(v), **rgb_indices(px)})
    t = pd.DataFrame(rows)
    for c in ["area_m2", "equiv_diameter_m", "solidity", "elongation"]:
        t[c] = gdf[c].values
    t.to_csv(cache, index=False)
    return t


def weak_labels(gdf) -> np.ndarray:
    """1 = planted stock, 0 = spontaneous vegetation, -1 = not used for training."""
    y = np.full(len(gdf), -1)
    planted = gdf.on_planting_line & gdf.core_found & (gdf.candidate_z >= 6)
    other = ~gdf.on_planting_line & (gdf.ndvi_anomaly_z >= 4)
    y[planted.values] = 1
    y[other.values] = 0
    return y


def spatial_groups(gdf, block_m=25.0) -> np.ndarray:
    gx = np.floor(gdf.centroid_x.values / block_m).astype(int)
    gy = np.floor(gdf.centroid_y.values / block_m).astype(int)
    return gx * 10000 + gy


def make_X(tab, emb=None):
    X = tab[TABULAR].astype(float).fillna(tab[TABULAR].astype(float).median()).values
    return X if emb is None else np.c_[X, emb]


def classifier(C=0.05):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return make_pipeline(StandardScaler(), LogisticRegression(C=C, max_iter=3000, class_weight="balanced"))


def oof_proba(X, y, groups, n_splits=5, C=0.05):
    """Out-of-fold P(planted) for labelled rows, using spatial block GroupKFold."""
    from sklearn.model_selection import GroupKFold
    p = np.full(len(y), np.nan)
    for tr, te in GroupKFold(n_splits=n_splits).split(X, y, groups):
        m = classifier(C).fit(X[tr], y[tr])
        p[te] = m.predict_proba(X[te])[:, 1]
    return p


def main(input_dir=DEFAULT_INPUT, backbone=DEFAULT_BACKBONE) -> dict:
    import geopandas as gpd
    from sklearn.metrics import balanced_accuracy_score, roc_auc_score

    out = out_dir("identification")
    gdf = gpd.read_file(OUT / "detection" / "plants.geojson")
    crops = load_crops(gdf, input_dir)
    tab = tabular_features(gdf)
    emb = embed(load_crown_crops(gdf, crops), backbone, tag="_crown")
    X = make_X(tab, emb)
    y = weak_labels(gdf)
    lab = y >= 0
    groups = spatial_groups(gdf)

    # Honest confidence for labelled rows = out-of-fold (spatial CV) probability
    p_oof = oof_proba(X[lab], y[lab], groups[lab])
    final = classifier().fit(X[lab], y[lab])
    p = final.predict_proba(X)[:, 1]
    p[lab] = p_oof

    conf = np.maximum(p, 1 - p)
    cls = np.where(p >= 0.5, PALM, OTHER)
    cls = np.where(conf < MIN_CONFIDENCE, UNCLASSIFIED, cls)

    res = gdf[["plant_id", "geometry"]].copy()
    res["predicted_class"] = cls
    res["prediction_confidence"] = conf.round(3)
    res["p_planted_palm"] = p.round(3)
    res["weak_label"] = np.select([y == 1, y == 0], [PALM, OTHER], "unlabelled")
    res["identification_model"] = IDENT_MODEL.format(backbone=backbone)
    res["identification_backbone"] = BACKBONES[backbone]
    res["model_version"] = IDENT_VERSION
    res["identification_inputs"] = ("RGB crown crop (6 mm source, 1.2 m window, drip line inpainted, "
                                     "background neutralised) + crown NDVI stats + RGB indices + geometry")
    res["experimental"] = True
    res["processing_date"] = PROCESSING_DATE
    res.to_file(out / "plant_identification.geojson", driver="GeoJSON")

    counts = {str(k): int(v) for k, v in zip(*np.unique(cls, return_counts=True))}
    summary = {
        "model": IDENT_MODEL.format(backbone=backbone), "backbone": BACKBONES[backbone], "version": IDENT_VERSION,
        "classes": [PALM, OTHER, UNCLASSIFIED], "min_confidence": MIN_CONFIDENCE,
        "n_plants": int(len(gdf)), "class_counts": counts,
        "identified_fraction": float((cls != UNCLASSIFIED).mean()),
        "weak_labels": {"planted": int((y == 1).sum()), "other": int((y == 0).sum()), "unlabelled": int((y < 0).sum())},
        "spatial_cv": {
            "folds": 5, "block_m": 25.0,
            "roc_auc": float(roc_auc_score(y[lab], p_oof)),
            "balanced_accuracy": float(balanced_accuracy_score(y[lab], p_oof >= 0.5)),
        },
        "label_agreement_on_unlabelled": {
            "on_line_unlabelled_predicted_palm": float((cls[(y < 0) & gdf.on_planting_line.values] == PALM).mean())
            if ((y < 0) & gdf.on_planting_line.values).any() else None,
        },
        "processing_date": PROCESSING_DATE,
    }
    write_json(out / "identification_summary.json", summary)
    examples(crops, cls, conf, out / "identification_examples.png")
    print({k: v for k, v in summary.items() if k not in ("classes",)})
    return summary


def examples(crops, cls, conf, path, n=8):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    rng = np.random.default_rng(1)
    fig, ax = plt.subplots(3, n, figsize=(2 * n, 6.6))
    for r, c in enumerate([PALM, OTHER, UNCLASSIFIED]):
        idx = np.flatnonzero(cls == c)
        pick = rng.choice(idx, size=min(n, len(idx)), replace=False) if len(idx) else []
        for j in range(n):
            a = ax[r, j]
            a.axis("off")
            if j < len(pick):
                a.imshow(crops[pick[j]])
                a.set_title(f"{conf[pick[j]]:.2f}", fontsize=8)
        ax[r, 0].text(-0.1, 0.5, c, transform=ax[r, 0].transAxes, rotation=90, va="center", ha="right", fontsize=9)
    plt.suptitle("Plant Identification examples (1.2 m crops, 6 mm source) - title = confidence")
    plt.tight_layout(); plt.savefig(path, dpi=80); plt.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=str(DEFAULT_INPUT))
    ap.add_argument("--backbone", default=DEFAULT_BACKBONE, choices=list(BACKBONES))
    a = ap.parse_args()
    main(a.input, a.backbone)
