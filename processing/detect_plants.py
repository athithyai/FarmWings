"""Stage 2b - plant detection / instance segmentation.

Pipeline (aligned 2.36 cm grid, RGB + co-registered NDVI):
  1. plant-ness surface = robust z of RGB darkness (drip lines removed by grey closing)
     + robust z of NDVI anomaly, both relative to the local soil background (sigma ~0.95 m)
  2. candidates = local maxima (one per 0.73 m window, z > 3)
  3. planting-line context from detect_lines.py: |distance| <= 0.24 m -> planted position;
     off-line candidates are kept only with a real vegetation signal (NDVI anomaly z >= 4)
  4. SAM 2.1 (hiera-large) point + box prompt per candidate on 1024 px tiles -> mask
  5. mask trimmed to the plant/pit core (fine plant-ness > 2.5 inside the SAM mask; if no core
     survives the SAM mask is kept and core_found=false), shape filter (area 0.02-1.4 m2,
     solidity >= 0.7, elongation <= 3), overlap suppression
  6. polygonise, attach geometry / line / confidence attributes

detection_confidence is a heuristic score in [0, 1]: geometric mean of SAM's predicted mask
IoU and the candidate strength 1 - exp(-(z - 3) / 2.5). It is NOT a calibrated probability.

Outputs (processing_outputs/detection/): plants.geojson, detection_summary.json, detection_preview.png
"""
from __future__ import annotations

from common import OUT, PROCESSING_DATE, out_dir, write_json  # noqa: I001 (sets PROJ_DATA first)

import cv2
import numpy as np
from scipy import ndimage as ndi

PIXEL_M = 0.0236
BG_SIGMA = 40          # px (~0.95 m) - local soil background scale
PEAK_SIGMA = 4         # px (~0.10 m) - smoothing of the plant-ness surface
PEAK_WINDOW = 31       # px (~0.73 m) - one candidate per window
PEAK_Z = 3.0           # robust z threshold for a candidate
LINE_KERNEL = 7        # px (~0.17 m) - grey closing removes drip lines (1-3 px wide) from brightness
BOX_HALF = 24          # px (~0.57 m) - half side of the SAM box prompt around a candidate
MIN_AREA_PX, MAX_AREA_PX = 40, 2500      # ~0.022 m2 .. ~1.4 m2
MIN_SOLIDITY, MAX_ELONGATION = 0.70, 3.0
ON_LINE_PX = 10        # px (~0.24 m) - candidate counts as on a planting line
OFFLINE_MIN_ZN = 4.0   # off-line candidates need this NDVI-anomaly z to be kept
TILE, TILE_STEP = 1024, 896
FINE_SIGMA = 1.5       # px - smoothing of the pixel-level plant-ness used to trim SAM masks
CORE_Z = 2.5           # fine plant-ness threshold for the plant/pit core inside a SAM mask

DETECTION_MODEL = "RGB+NDVI plant-ness candidates + drip-line context + SAM 2.1 hiera-large prompts"
DETECTION_VERSION = "0.1.0"


def _nan_gauss(a: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian blur that ignores NaNs (normalised convolution)."""
    m = np.isfinite(a).astype(np.float32)
    num = cv2.GaussianBlur(np.where(m > 0, a, 0).astype(np.float32), (0, 0), sigma)
    den = cv2.GaussianBlur(m, (0, 0), sigma)
    with np.errstate(invalid="ignore", divide="ignore"):
        return num / den


def _robust_z(a: np.ndarray, ref=None) -> np.ndarray:
    v = a[np.isfinite(a)] if ref is None else ref
    med = np.median(v)
    mad = np.median(np.abs(v - med)) * 1.4826 + 1e-6
    return (a - med) / mad


def plantness(rgb: np.ndarray, ndvi: np.ndarray, scales=None, components: bool = False):
    """Per-pixel 'plant-ness' from RGB darkness and NDVI, both relative to the local soil background.

    Planted positions are dark mulched pits and/or green crowns on bright sand, so a pixel
    scores high when it is darker than its surroundings or greener (NDVI) than them.
    """
    valid = np.isfinite(ndvi) & (rgb.sum(-1) > 0)
    L = rgb.astype(np.float32).mean(-1)
    # Grey closing fills dark features thinner than the kernel (the drip-irrigation lines)
    L = cv2.morphologyEx(L, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (LINE_KERNEL, LINE_KERNEL)))
    L = np.where(valid, L, np.nan)
    dark = 1.0 - L / _nan_gauss(L, BG_SIGMA)
    nd = np.where(valid, ndvi, np.nan)
    nd_anom = nd - _nan_gauss(nd, BG_SIGMA)
    if scales is None:
        zd, zn = _robust_z(dark), _robust_z(nd_anom)
    else:
        zd = (dark - scales["dark_med"]) / scales["dark_mad"]
        zn = (nd_anom - scales["nd_med"]) / scales["nd_mad"]
    P = np.clip(zd, -3, 12) + np.clip(zn, -3, 12)
    P = _nan_gauss(np.where(valid, P, np.nan), PEAK_SIGMA)
    P = np.where(valid, P, np.nan)
    if components:
        fine = _nan_gauss(np.where(valid, np.clip(zd, -3, 12) + np.clip(zn, -3, 12), np.nan), FINE_SIGMA)
        zd = _nan_gauss(np.where(valid, np.clip(zd, -3, 12), np.nan), PEAK_SIGMA)
        zn = _nan_gauss(np.where(valid, np.clip(zn, -3, 12), np.nan), PEAK_SIGMA)
        return P.astype(np.float32), zd.astype(np.float32), zn.astype(np.float32), fine.astype(np.float32)
    return P


def candidate_points(P: np.ndarray, valid: np.ndarray, z=PEAK_Z) -> np.ndarray:
    """Local maxima of the plant-ness surface -> (row, col, score) candidates."""
    Pf = np.where(np.isfinite(P), P, -99)
    mx = ndi.maximum_filter(Pf, size=PEAK_WINDOW)
    peaks = (Pf == mx) & (Pf > z) & valid
    r, c = np.nonzero(peaks)
    return np.c_[r, c, Pf[r, c]]


def load_sam2(size: str = "large"):
    from huggingface_hub import hf_hub_download
    from sam2.build_sam import build_sam2
    from sam2.sam2_image_predictor import SAM2ImagePredictor
    cfg = {"large": ("facebook/sam2.1-hiera-large", "sam2.1_hiera_large.pt", "configs/sam2.1/sam2.1_hiera_l.yaml"),
           "tiny": ("facebook/sam2.1-hiera-tiny", "sam2.1_hiera_tiny.pt", "configs/sam2.1/sam2.1_hiera_t.yaml")}[size]
    ckpt = hf_hub_download(cfg[0], cfg[1])
    model = build_sam2(cfg[2], ckpt, device="cuda")
    return SAM2ImagePredictor(model), model


def mask_shape_ok(mask: np.ndarray):
    """Area / solidity / elongation filter. Returns (ok, props)."""
    area = int(mask.sum())
    if not (MIN_AREA_PX <= area <= MAX_AREA_PX):
        return False, None
    cnts, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    c = max(cnts, key=cv2.contourArea)
    hull = cv2.contourArea(cv2.convexHull(c)) + 1e-6
    solidity = cv2.contourArea(c) / hull
    (_, _), (w, h), _ = cv2.minAreaRect(c)
    elong = max(w, h) / max(min(w, h), 1.0)
    ok = solidity >= MIN_SOLIDITY and elong <= MAX_ELONGATION
    return ok, {"area_px": area, "solidity": float(solidity), "elongation": float(elong), "contour": c}


def refine_mask(mask: np.ndarray, fine: np.ndarray, y: int, x: int):
    """Trim a SAM mask to its plant/pit core (fine plant-ness > CORE_Z); None if no core survives."""
    core = mask & (np.nan_to_num(fine, nan=-9) > CORE_Z)
    core = cv2.morphologyEx(core.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, lab = cv2.connectedComponents(core)
    if n <= 1:
        return None
    ids, counts = np.unique(lab[lab > 0], return_counts=True)
    k = lab[y, x] if lab[y, x] > 0 else ids[np.argmax(counts)]
    core = ndi.binary_fill_holes(lab == k)
    return core if core.sum() >= MIN_AREA_PX else None


def segment_candidates(predictor, pts: np.ndarray, shape, fine: np.ndarray | None = None) -> list[dict]:
    """SAM 2.1 point+box prompts for each candidate (predictor must already hold the image).

    With `fine` (pixel plant-ness for the same tile) the chosen mask is trimmed to its core.
    """
    import torch
    H, W = shape
    out = []
    for (y, x, z) in pts:
        box = np.array([max(x - BOX_HALF, 0), max(y - BOX_HALF, 0), min(x + BOX_HALF, W - 1), min(y + BOX_HALF, H - 1)])
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            m, sc, _ = predictor.predict(point_coords=np.array([[x, y]]), point_labels=np.array([1]),
                                         box=box[None], multimask_output=True)
        best = None
        for i in np.argsort(-sc):
            mk = m[i].astype(bool)
            if not mk[int(y), int(x)]:
                continue
            ok, props = mask_shape_ok(mk)
            if ok:
                core_found = False
                if fine is not None:
                    core = refine_mask(mk, fine, int(y), int(x))
                    ok2, props2 = mask_shape_ok(core) if core is not None else (False, None)
                    if ok2:
                        mk, props, core_found = core, props2, True
                best = {"mask": mk, "sam_iou": float(sc[i]), "peak_z": float(z), "row": int(y), "col": int(x),
                        "core_found": core_found, **props}
                break
        if best:
            out.append(best)
    return out


def dedupe(objs: list[dict], iou_thr=0.3) -> list[dict]:
    """Greedy suppression of overlapping masks (keep higher peak score)."""
    objs = sorted(objs, key=lambda o: -o["peak_z"])
    kept = []
    for o in objs:
        if all((o["mask"] & k["mask"]).sum() / min(o["area_px"], k["area_px"]) < iou_thr for k in kept
               if abs(o["row"] - k["row"]) < 2 * BOX_HALF and abs(o["col"] - k["col"]) < 2 * BOX_HALF):
            kept.append(o)
    return kept


def detection_confidence(sam_iou: float, z: float) -> float:
    strength = 1.0 - np.exp(-max(z - PEAK_Z, 0.0) / 2.5)
    return float(np.sqrt(max(sam_iou, 0.0) * strength))


def main() -> dict:
    import geopandas as gpd
    import rasterio
    from shapely.geometry import Polygon
    from shapely.strtree import STRtree
    from detect_lines import LineModel

    out = out_dir("detection")
    with rasterio.open(OUT / "aligned" / "rgb_aligned.tif") as d:
        rgb = np.moveaxis(d.read([1, 2, 3]), 0, -1)
        transform, crs = d.transform, d.crs
    with rasterio.open(OUT / "aligned" / "ndvi_coreg.tif") as d:
        ndvi = d.read(1)
    ndvi = np.where(ndvi < -2, np.nan, ndvi).astype(np.float32)
    valid = np.isfinite(ndvi)
    H, W = ndvi.shape

    print("plant-ness surface ...")
    P, zd, zn, fine = plantness(rgb, ndvi, components=True)
    pts = candidate_points(P, valid)
    r, c = pts[:, 0].astype(int), pts[:, 1].astype(int)
    lines = LineModel.load(OUT / "lines" / "lines_model.npz")
    li, dist, along = lines.nearest(r, c)
    on_line = np.abs(dist) <= ON_LINE_PX
    keep = on_line | (zn[r, c] >= OFFLINE_MIN_ZN)
    print(f"candidates {len(pts)}  on-line {on_line.sum()}  off-line vegetated {(keep & ~on_line).sum()}")
    # columns: row, col, z, line_idx, dist_px, along_px, darkness_z, ndvi_anomaly_z, on_line
    cand = np.c_[pts, li, dist, along, zd[r, c], zn[r, c], on_line][keep]
    del P, zd, zn

    predictor, _ = load_sam2("large")
    tiles: dict[tuple[int, int], list[int]] = {}
    for k, (y, x) in enumerate(cand[:, :2]):
        ty = min(int(y // TILE_STEP), (H - 1) // TILE_STEP)
        tx = min(int(x // TILE_STEP), (W - 1) // TILE_STEP)
        tiles.setdefault((ty, tx), []).append(k)
    objs = []
    for n, ((ty, tx), idx) in enumerate(sorted(tiles.items())):
        oy = int(np.clip(ty * TILE_STEP - 64, 0, H - TILE))
        ox = int(np.clip(tx * TILE_STEP - 64, 0, W - TILE))
        predictor.set_image(rgb[oy:oy + TILE, ox:ox + TILE])
        local = cand[idx, :3].copy()
        local[:, 0] -= oy
        local[:, 1] -= ox
        lut = {(int(a), int(b)): k for k, (a, b) in zip(idx, local[:, :2])}
        for o in segment_candidates(predictor, local, (TILE, TILE), fine[oy:oy + TILE, ox:ox + TILE]):
            cc = o["contour"][:, 0, :].astype(float)
            if len(cc) < 3:
                continue
            xs, ys = rasterio.transform.xy(transform, cc[:, 1] + oy, cc[:, 0] + ox, offset="center")
            poly = Polygon(np.c_[xs, ys]).buffer(0)
            if poly.is_empty:
                continue
            if poly.geom_type == "MultiPolygon":
                poly = max(poly.geoms, key=lambda g: g.area)
            objs.append({"geometry": poly.simplify(0.004), "cand": lut[(o["row"], o["col"])],
                         "sam_iou": o["sam_iou"], "solidity": o["solidity"], "elongation": o["elongation"],
                         "core_found": o["core_found"]})
        if n % 10 == 0:
            print(f"  tile {n + 1}/{len(tiles)}  objects {len(objs)}")

    # Overlap suppression across tile borders / neighbouring prompts (keep stronger candidate)
    objs.sort(key=lambda o: -cand[o["cand"], 2])
    tree = STRtree([o["geometry"] for o in objs])
    dropped = np.zeros(len(objs), bool)
    for i, o in enumerate(objs):
        if dropped[i]:
            continue
        for j in tree.query(o["geometry"]):
            if j <= i or dropped[j]:
                continue
            g2 = objs[j]["geometry"]
            if o["geometry"].intersection(g2).area / min(o["geometry"].area, g2.area) >= 0.3:
                dropped[j] = True
    objs = [o for i, o in enumerate(objs) if not dropped[i]]

    rows = []
    for o in objs:
        y, x, z, li_, d_, al_, zd_, zn_, onl = cand[o["cand"]]
        g = o["geometry"]
        rows.append({
            "line_idx": int(li_), "along_px": float(al_),
            "line_id": f"L{int(li_) + 1:03d}" if li_ >= 0 else None,
            "dist_to_line_m": round(float(d_) * PIXEL_M, 3),
            "on_planting_line": bool(onl),
            "position_type": "Planting line" if onl else "Between lines",
            "area_m2": round(g.area, 4),
            "equiv_diameter_m": round(2 * np.sqrt(g.area / np.pi), 3),
            "perimeter_m": round(g.length, 3),
            "solidity": round(o["solidity"], 3), "elongation": round(o["elongation"], 3),
            "candidate_z": round(float(z), 2), "darkness_z": round(float(zd_), 2),
            "ndvi_anomaly_z": round(float(zn_), 2),
            "sam_iou": round(o["sam_iou"], 3),
            "core_found": bool(o["core_found"]),
            "detection_confidence": round(detection_confidence(o["sam_iou"], float(z)), 3),
            "geometry": g,
        })
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs=crs)
    gdf = gdf.sort_values(["on_planting_line", "line_idx", "along_px"], ascending=[False, True, True]).reset_index(drop=True)
    gdf.insert(0, "plant_id", [f"P{i + 1:05d}" for i in range(len(gdf))])
    cen = gdf.geometry.centroid
    gdf["centroid_x"], gdf["centroid_y"] = cen.x.round(3), cen.y.round(3)
    ll = cen.to_crs(4326)
    gdf["lon"], gdf["lat"] = ll.x.round(7), ll.y.round(7)
    gdf["detection_model"] = DETECTION_MODEL
    gdf["detection_model_version"] = DETECTION_VERSION
    gdf["processing_date"] = PROCESSING_DATE
    gdf = gdf.drop(columns=["line_idx"])
    gdf.to_file(out / "plants.geojson", driver="GeoJSON")

    summary = {
        "n_candidates": int(len(pts)), "n_candidates_on_line": int(on_line.sum()),
        "n_candidates_kept": int(len(cand)), "n_plants": int(len(gdf)),
        "n_on_planting_line": int(gdf.on_planting_line.sum()),
        "n_between_lines": int((~gdf.on_planting_line).sum()),
        "area_m2_median": float(gdf.area_m2.median()), "area_m2_p90": float(gdf.area_m2.quantile(0.9)),
        "detection_confidence_median": float(gdf.detection_confidence.median()),
        "core_found_fraction": float(gdf.core_found.mean()),
        "parameters": {"PEAK_Z": PEAK_Z, "PEAK_WINDOW_px": PEAK_WINDOW, "BG_SIGMA_px": BG_SIGMA,
                       "ON_LINE_PX": ON_LINE_PX, "OFFLINE_MIN_ZN": OFFLINE_MIN_ZN,
                       "area_px": [MIN_AREA_PX, MAX_AREA_PX], "min_solidity": MIN_SOLIDITY,
                       "max_elongation": MAX_ELONGATION, "sam_box_half_px": BOX_HALF},
        "model": DETECTION_MODEL, "version": DETECTION_VERSION,
    }
    write_json(out / "detection_summary.json", summary)
    preview(gdf, out / "detection_preview.png")
    print({k: v for k, v in summary.items() if k != "parameters"})
    return summary


def preview(gdf, path, window=(4245, 2448, 1024)):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import rasterio
    from rasterio.windows import Window
    c0, r0, s = window
    with rasterio.open(OUT / "aligned" / "rgb_aligned.tif") as d:
        img = np.moveaxis(d.read([1, 2, 3], window=Window(c0, r0, s, s)), 0, -1)
        x0, y0 = d.transform * (c0, r0)
        x1, y1 = d.transform * (c0 + s, r0 + s)
    fig, ax = plt.subplots(figsize=(10, 10))
    ax.imshow(img, extent=[x0, x1, y1, y0])
    sub = gdf.cx[x0:x1, y1:y0]
    if sub.on_planting_line.any():
        sub[sub.on_planting_line].boundary.plot(ax=ax, color="#ff00ff", lw=1)
    if (~sub.on_planting_line).any():
        sub[~sub.on_planting_line].boundary.plot(ax=ax, color="#00e5ff", lw=1)
    ax.set_xlim(x0, x1); ax.set_ylim(y1, y0); ax.axis("off")
    ax.set_title("Detected plants: magenta = planting line, cyan = between lines")
    plt.tight_layout(); plt.savefig(path, dpi=90); plt.close()


if __name__ == "__main__":
    main()
