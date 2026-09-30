"""Detection model trial on representative tiles (not part of the pipeline run).

Compares, on the same 24 m x 24 m tiles of the aligned 2.36 cm grid:
  1. DeepForest pretrained tree-crown model (weecology/deepforest-tree), native + 10 cm resample
  2. SAM 2.1 automatic mask generation (hiera-large)
  3. RGB+NDVI blob candidates -> SAM 2.1 point-prompted masks
Writes processing_outputs/experiments/detection_trial_<tile>.png and detection_trial.json
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import OUT, out_dir, write_json  # noqa: E402,I001

import cv2  # noqa: E402
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import rasterio  # noqa: E402
import torch  # noqa: E402
from rasterio.windows import Window  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from detect_plants import candidate_points, plantness, load_sam2, segment_candidates, dedupe  # noqa: E402

TILES = {"volunteer_patch": (7734, 3267), "planting_grid": (4245, 2448), "sparse": (5303, 4245)}
S = 1024


def read_tile(c, r):
    with rasterio.open(OUT / "aligned" / "rgb_aligned.tif") as d:
        rgb = d.read([1, 2, 3], window=Window(c, r, S, S))
    with rasterio.open(OUT / "aligned" / "ndvi_coreg.tif") as d:
        ndvi = d.read(1, window=Window(c, r, S, S))
    return np.moveaxis(rgb, 0, -1), np.where(ndvi < -2, np.nan, ndvi)


def run_deepforest(img):
    from deepforest import main as dfm
    m = dfm.deepforest()
    m.load_model("weecology/deepforest-tree")
    m.config["score_thresh"] = 0.1
    res = {}
    for name, scale in [("native_2.4cm", 1.0), ("resampled_10cm", 0.236)]:
        im = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale != 1 else img
        df = m.predict_image(image=im.astype("float32"))
        boxes = []
        if df is not None:
            b = df[["xmin", "ymin", "xmax", "ymax"]].to_numpy() / scale
            boxes = np.c_[b, df["score"].to_numpy()].tolist()
        res[name] = boxes
    return res


def main(quick: bool = False):
    """quick=True skips the DeepForest / SAM-automatic baselines (already recorded in *_v1_baselines.json)."""
    out = out_dir("experiments")
    sam_pred, sam_model = load_sam2()
    from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator
    amg = SAM2AutomaticMaskGenerator(sam_model, points_per_side=64, pred_iou_thresh=0.7,
                                     stability_score_thresh=0.85, min_mask_region_area=40)
    summary = {}
    for name, (c, r) in TILES.items():
        rgb, ndvi = read_tile(c, r)
        if quick:
            df, amg_masks = {"native_2.4cm": [], "resampled_10cm": []}, []
        else:
            df = run_deepforest(rgb)
            with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
                amg_masks = amg.generate(rgb)
        P = plantness(rgb, ndvi)
        pts = candidate_points(P, np.isfinite(ndvi))
        sam_pred.set_image(rgb)
        prompted = [(o["mask"], o["sam_iou"]) for o in dedupe(segment_candidates(sam_pred, pts, P.shape))]

        fig, ax = plt.subplots(1, 5, figsize=(30, 6.5))
        ax[0].imshow(rgb); ax[0].set_title(f"RGB {name}")
        ax[1].imshow(ndvi, cmap="RdYlGn", vmin=0, vmax=0.6); ax[1].set_title("NDVI (co-registered)")
        ax[2].imshow(rgb)
        for k, col in [("native_2.4cm", "red"), ("resampled_10cm", "cyan")]:
            for x0, y0, x1, y1, s in df[k]:
                ax[2].add_patch(plt.Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False, color=col, lw=1))
        ax[2].set_title(f"DeepForest: {len(df['native_2.4cm'])} (2.4cm, red) / {len(df['resampled_10cm'])} (10cm, cyan)")
        ov = rgb.copy()
        rng = np.random.default_rng(0)
        for mm in amg_masks:
            ov[mm["segmentation"]] = (0.5 * ov[mm["segmentation"]] + 0.5 * rng.integers(0, 255, 3)).astype(np.uint8)
        ax[3].imshow(ov); ax[3].set_title(f"SAM2.1 automatic: {len(amg_masks)} masks")
        ov = rgb.copy()
        for mm, _ in prompted:
            cnts, _ = cv2.findContours(mm.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(ov, cnts, -1, (255, 0, 255), 2)
        ax[4].imshow(ov); ax[4].scatter(pts[:, 1], pts[:, 0], s=6, c="yellow")
        ax[4].set_title(f"Blob candidates {len(pts)} -> SAM2.1 prompted {len(prompted)}")
        for a in ax:
            a.axis("off")
        plt.tight_layout(); plt.savefig(out / f"detection_trial_{name}.png", dpi=70); plt.close()
        cv2.imwrite(str(out / f"detection_zoom_{name}.png"), cv2.cvtColor(ov[300:700, 300:700], cv2.COLOR_RGB2BGR))
        summary[name] = {"deepforest_native": len(df["native_2.4cm"]), "deepforest_10cm": len(df["resampled_10cm"]),
                         "sam_amg_masks": len(amg_masks),
                         "sam_amg_median_area_px": float(np.median([m["area"] for m in amg_masks])) if amg_masks else 0,
                         "blob_candidates": int(len(pts)), "sam_prompted": len(prompted)}
        print(name, summary[name])
    write_json(out / ("detection_trial_quick.json" if quick else "detection_trial.json"), summary)


if __name__ == "__main__":
    main(quick="--quick" in sys.argv)
