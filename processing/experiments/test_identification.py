"""Identification model trial: which inputs / backbone separate planted stock from
spontaneous vegetation best? Spatial block cross-validation on weak labels.

Also tests CLIP zero-shot prompts (does a generic vision-language model 'know' a palm
seedling from above?). Writes processing_outputs/experiments/identification_trial.json
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import DEFAULT_INPUT, OUT, out_dir, write_json  # noqa: E402,I001

import geopandas as gpd  # noqa: E402
import numpy as np  # noqa: E402
from sklearn.metrics import balanced_accuracy_score, roc_auc_score  # noqa: E402

from identify_plants import (BACKBONES, embed, load_crops, load_crown_crops, make_X,  # noqa: E402
                             oof_proba, spatial_groups, tabular_features, weak_labels)


def clip_zero_shot(crops, lab_idx):
    import torch
    from transformers import CLIPModel, CLIPProcessor
    repo = BACKBONES["clip-vit-l14"]
    model = CLIPModel.from_pretrained(repo, local_files_only=True).cuda().eval().half()
    proc = CLIPProcessor.from_pretrained(repo, local_files_only=True)
    prompts = ["an aerial top-down photo of a young palm seedling planted in a pit",
               "an aerial top-down photo of small wild weeds and grass on sand"]
    with torch.inference_mode():
        t = proc(text=prompts, return_tensors="pt", padding=True).to("cuda")
        tf = model.get_text_features(**t)
        tf = getattr(tf, "pooler_output", tf).float()
        tf = tf / tf.norm(dim=-1, keepdim=True)
        ps = []
        for i in range(0, len(lab_idx), 64):
            x = proc(images=list(crops[lab_idx[i:i + 64]]), return_tensors="pt")["pixel_values"].cuda().half()
            f = model.get_image_features(pixel_values=x)
            f = getattr(f, "pooler_output", f).float()
            f = f / f.norm(dim=-1, keepdim=True)
            ps.append((100 * f @ tf.T).softmax(-1)[:, 0].cpu().numpy())
    return np.concatenate(ps)


def main():
    out = out_dir("experiments")
    gdf = gpd.read_file(OUT / "detection" / "plants.geojson")
    crops = load_crops(gdf, DEFAULT_INPUT)
    tab = tabular_features(gdf)
    y = weak_labels(gdf)
    lab = y >= 0
    g = spatial_groups(gdf)
    res = {"n_labelled": int(lab.sum()), "n_planted": int((y == 1).sum()), "n_other": int((y == 0).sum())}

    def score(name, X):
        p = oof_proba(X[lab], y[lab], g[lab])
        res[name] = {"roc_auc": round(float(roc_auc_score(y[lab], p)), 4),
                     "balanced_accuracy": round(float(balanced_accuracy_score(y[lab], p >= 0.5)), 4)}
        print(name, res[name])

    score("ndvi_rgb_geometry_only", make_X(tab))
    for b in BACKBONES:
        emb = embed(crops, b)
        score(f"{b}_only", emb)
        score(f"{b}+tabular", make_X(tab, emb))
    # Crown-focused crops: drip line inpainted, context outside the crown neutralised
    cc = load_crown_crops(gdf, crops)
    for b in BACKBONES:
        emb = embed(cc, b, tag="_crown")
        score(f"{b}_crown_only", emb)
        score(f"{b}_crown+tabular", make_X(tab, emb))
    pz = clip_zero_shot(crops, np.flatnonzero(lab))
    res["clip_zero_shot"] = {"roc_auc": round(float(roc_auc_score(y[lab], pz)), 4),
                             "balanced_accuracy": round(float(balanced_accuracy_score(y[lab], pz >= 0.5)), 4)}
    print("clip_zero_shot", res["clip_zero_shot"])
    write_json(out / "identification_trial.json", res)


if __name__ == "__main__":
    main()
