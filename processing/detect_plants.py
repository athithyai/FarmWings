"""Stage 2b - plant detection / instance segmentation.

Pipeline (aligned 2.36 cm grid, RGB + co-registered NDVI):
  1. plant-ness surface = robust z of RGB darkness (drip lines removed by grey closing)
     + robust z of NDVI anomaly, both relative to the local soil background (sigma ~0.95 m)
  2. candidates = local maxima (one per 0.73 m window, z > 3)
  3. planting-line context from detect_lines.py: candidates within 0.24 m of a line are
     segmented; off-line candidates only with a vegetation signal (NDVI anomaly z >= 4)
  4. SAM 2.1 (hiera-large) point + box prompt per candidate on 1024 px tiles -> mask
  5. SAM runs on rgb_fine.tif (1.18 cm, 2x the analysis grid) so fine saplings keep their shape;
     mask trimmed to the plant/pit core (fine plant-ness > 2.5 inside the SAM mask; if no core
     survives the SAM mask is kept and core_found=false), shape filter (area 0.01-1.4 m2,
     solidity >= 0.7, elongation <= 3), overlap suppression
  6. recall pass: NDVI vegetation patches (NDVI above local soil, robust z >= 3; >= 0.02 m2 on a
     planting line, >= 0.05 m2 between lines)
     that no object covers get their own SAM 2.1 prompt at the patch's greenest pixel; if SAM's
     mask fails the shape filter the patch outline is used (detection_source / outline fields)
  7. polygonise (-> plants_raw.geojson), then finalize(): planted position = polygon centroid
     within 0.31 m of a line AND the drip line locally visible (score >= 5, see
     detect_lines.LineModel.visibility); other objects are 'Between lines' and kept only with
     NDVI anomaly z >= 4. Run `python processing/detect_plants.py --positions-only` to redo
     just this step from plants_raw.geojson.

detection_confidence is a heuristic score in [0, 1]: geometric mean of SAM's predicted mask
IoU and the candidate strength 1 - exp(-(z - 3) / 2.5). It is NOT a calibrated probability.

Outputs (processing_outputs/detection/): plants_raw.geojson, plants.geojson, detection_summary.json,
detection_preview.png
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
MIN_AREA_M2, MAX_AREA_M2 = 0.010, 1.4   # ~11 cm .. ~1.3 m equivalent diameter (fine saplings)
BETWEEN_MIN_AREA_M2 = 0.02              # between-line objects must be at least this large
MERGE_M = 1.0          # planted objects closer than this along a line are one planting position
TOUCH_M = 0.10         # fragments this close to the main plant outline are joined to it
PROMOTE_M = 0.40      # vegetated object within this of an expected (gap) spot becomes its plant
PIPE_MIN_ELONGATION, PIPE_MAX_ANGLE, PIPE_MAX_NDVI_Z = 2.0, 20.0, 3.0
RHYTHM_TOL = 0.30     # max deviation from a whole number of plant spacings between chosen plants
RHYTHM_PENALTY = 2.0  # score penalty per spacing of deviation
W_CONF, W_VEG, W_LINE = 0.5, 0.5, 0.5   # evidence weights of an object
SAM_SCALE = 2          # SAM runs on rgb_fine.tif, 2x finer than the analysis grid (1.18 cm)
MIN_SOLIDITY, MAX_ELONGATION = 0.70, 3.0
ON_LINE_PX = 10        # px (~0.24 m) - candidate counts as on a planting line
OFFLINE_MIN_ZN = 4.0   # off-line candidates need this NDVI-anomaly z to be kept
TILE, TILE_STEP = 1024, 896
CENTROID_LINE_PX = 13  # px (~0.31 m) - final polygon centroid distance to a planting line
from detect_lines import VIS_MIN as LINE_VISIBILITY_MIN  # noqa: E402 local drip-line visibility threshold
RECALL_Z = 3.0         # NDVI-above-soil robust z defining a vegetation patch (recall pass)
RECALL_MIN_AREA_M2 = 0.05  # smallest uncovered vegetation patch between lines that gets a detection
RECALL_MIN_AREA_ON_LINE_M2 = 0.02  # ...and on a planting line (fine saplings)
SOURCE_CANDIDATE = "RGB+NDVI candidate"
SOURCE_NDVI = "NDVI vegetation patch"
FINE_SIGMA = 1.5       # px - smoothing of the pixel-level plant-ness used to trim SAM masks
CORE_Z = 2.5           # fine plant-ness threshold for the plant/pit core inside a SAM mask
CORE_Z_RELAXED = 1.5   # second try for faint saplings
NO_CORE_MAX_M2 = 0.15  # a SAM mask without any plant-ness core is only kept when it is this small
PATCH_MIN_SOLIDITY, PATCH_MAX_ELONGATION = 0.5, 4.0  # NDVI patch outlines (leafy saplings are ragged)

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


def mask_shape_ok(mask: np.ndarray, scale: int = 1, min_solidity: float = None, max_elongation: float = None):
    """Area / solidity / elongation filter. Returns (ok, props). scale = grid factor vs the analysis grid."""
    min_solidity = MIN_SOLIDITY if min_solidity is None else min_solidity
    max_elongation = MAX_ELONGATION if max_elongation is None else max_elongation
    area = int(mask.sum())
    px_m2 = (PIXEL_M / scale) ** 2
    if not (MIN_AREA_M2 / px_m2 <= area <= MAX_AREA_M2 / px_m2):
        return False, None
    cnts, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    c = max(cnts, key=cv2.contourArea)
    hull = cv2.contourArea(cv2.convexHull(c)) + 1e-6
    solidity = cv2.contourArea(c) / hull
    (_, _), (w, h), _ = cv2.minAreaRect(c)
    elong = max(w, h) / max(min(w, h), 1.0)
    ok = solidity >= min_solidity and elong <= max_elongation
    return ok, {"area_px": area, "solidity": float(solidity), "elongation": float(elong), "contour": c}


def refine_mask(mask: np.ndarray, fine: np.ndarray, y: int, x: int, scale: int = 1, thr: float = None):
    """Trim a SAM mask to its plant/pit core (fine plant-ness > thr); None if no core survives."""
    core = mask & (np.nan_to_num(fine, nan=-9) > (CORE_Z if thr is None else thr))
    kk = 2 * scale + 3
    core = cv2.morphologyEx(core.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((kk, kk), np.uint8))
    n, lab = cv2.connectedComponents(core)
    if n <= 1:
        return None
    ids, counts = np.unique(lab[lab > 0], return_counts=True)
    k = lab[y, x] if lab[y, x] > 0 else ids[np.argmax(counts)]
    core = ndi.binary_fill_holes(lab == k)
    return core if core.sum() >= MIN_AREA_M2 / (PIXEL_M / scale) ** 2 else None


def segment_candidates(predictor, pts: np.ndarray, shape, fine: np.ndarray | None = None,
                       scale: int = 1) -> list[dict]:
    """SAM 2.1 point+box prompts for each candidate (predictor must already hold the image).

    With `fine` (pixel plant-ness for the same tile, same grid) the mask is trimmed to its core.
    scale = how many times finer the SAM grid is than the analysis grid.
    """
    import torch
    H, W = shape
    bh = BOX_HALF * scale
    out = []
    for (y, x, z) in pts:
        box = np.array([max(x - bh, 0), max(y - bh, 0), min(x + bh, W - 1), min(y + bh, H - 1)])
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            m, sc, _ = predictor.predict(point_coords=np.array([[x, y]]), point_labels=np.array([1]),
                                         box=box[None], multimask_output=True)
        best = None
        for i in np.argsort(-sc):
            mk = m[i].astype(bool)
            if not mk[int(y), int(x)]:
                continue
            ok, props = mask_shape_ok(mk, scale)
            if ok:
                core_found = False
                if fine is not None:
                    # the outline must follow plant evidence: trim to the core, relax once, and never
                    # keep a large SAM region that has no plant-ness core (it spreads over sand)
                    for thr in (CORE_Z, CORE_Z_RELAXED):
                        core = refine_mask(mk, fine, int(y), int(x), scale, thr)
                        ok2, props2 = mask_shape_ok(core, scale) if core is not None else (False, None)
                        if ok2:
                            mk, props, core_found = core, props2, True
                            break
                    if not core_found and props["area_px"] * (PIXEL_M / scale) ** 2 > NO_CORE_MAX_M2:
                        continue
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
    del zd, zn

    predictor, _ = load_sam2("large")
    with rasterio.open(OUT / "aligned" / "rgb_fine.tif") as d:
        rgb_hi = np.moveaxis(d.read([1, 2, 3]), 0, -1)
        transform_hi = d.transform
    H2, W2 = rgb_hi.shape[:2]
    S = SAM_SCALE

    def sam_pass(cand_rows, fine_img, label):
        """SAM 2.1 point+box prompts on the fine RGB grid, tile by tile. Returns objects with 'cand' index."""
        tiles: dict[tuple[int, int], list[int]] = {}
        for k, (y, x) in enumerate(cand_rows[:, :2] * S):
            ty = min(int(y // TILE_STEP), (H2 - 1) // TILE_STEP)
            tx = min(int(x // TILE_STEP), (W2 - 1) // TILE_STEP)
            tiles.setdefault((ty, tx), []).append(k)
        found = []
        for n, ((ty, tx), idx) in enumerate(sorted(tiles.items())):
            oy = int(np.clip(ty * TILE_STEP - 64, 0, H2 - TILE))
            ox = int(np.clip(tx * TILE_STEP - 64, 0, W2 - TILE))
            predictor.set_image(rgb_hi[oy:oy + TILE, ox:ox + TILE])
            local = cand_rows[idx, :3].copy()
            local[:, 0] = local[:, 0] * S - oy
            local[:, 1] = local[:, 1] * S - ox
            lut = {(int(a), int(b)): k for k, (a, b) in zip(idx, local[:, :2])}
            fine_tile = None
            if fine_img is not None:   # plant-ness is on the analysis grid: upsample the matching window
                cy0, cx0 = oy // S, ox // S
                win = fine_img[cy0:cy0 + TILE // S + 1, cx0:cx0 + TILE // S + 1]
                up = cv2.resize(win, (win.shape[1] * S, win.shape[0] * S), interpolation=cv2.INTER_LINEAR)
                fine_tile = up[oy - cy0 * S:oy - cy0 * S + TILE, ox - cx0 * S:ox - cx0 * S + TILE]
            for o in segment_candidates(predictor, local, (TILE, TILE), fine_tile, scale=S):
                poly = contour_polygon(o["contour"], oy, ox, transform_hi)
                if poly is None:
                    continue
                found.append({"geometry": poly, "cand": lut[(o["row"], o["col"])], "sam_iou": o["sam_iou"],
                              "solidity": o["solidity"], "elongation": o["elongation"],
                              "core_found": o["core_found"]})
            if n % 10 == 0:
                print(f"  {label}: tile {n + 1}/{len(tiles)}  objects {len(found)}")
        return found

    def contour_polygon(contour, oy, ox, tr):
        cc = contour[:, 0, :].astype(float)
        if len(cc) < 3:
            return None
        xs, ys = rasterio.transform.xy(tr, cc[:, 1] + oy, cc[:, 0] + ox, offset="center")
        poly = Polygon(np.c_[xs, ys]).buffer(0)
        if poly.is_empty:
            return None
        if poly.geom_type == "MultiPolygon":
            poly = max(poly.geoms, key=lambda g: g.area)
        return poly.simplify(0.003)

    # ---- pass 1: RGB + NDVI plant-ness candidates
    objs = sam_pass(cand, fine, "candidates")
    for o in objs:
        o["source"] = SOURCE_CANDIDATE
    objs = suppress_overlaps(objs, lambda o: (0, -cand[o["cand"], 2]))

    # ---- pass 2 (recall): NDVI vegetation patches that no object covers yet
    rcand, rmasks = ndvi_recall_candidates(ndvi, P, objs, transform, lines)
    print(f"NDVI recall: {len(rcand)} uncovered vegetation patches")
    robjs = sam_pass(rcand, fine, "recall") if len(rcand) else []
    got = {o["cand"] for o in robjs}
    n_fallback = 0
    for k in range(len(rcand)):
        if k in got:
            continue
        patch = cv2.morphologyEx(rmasks[k][0], cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        patch = ndi.binary_fill_holes(patch).astype(np.uint8)
        ok, props = mask_shape_ok(patch, 1, PATCH_MIN_SOLIDITY, PATCH_MAX_ELONGATION)
        if not ok:
            continue
        poly = contour_polygon(props["contour"], rmasks[k][1], rmasks[k][2], transform)
        if poly is None:
            continue
        robjs.append({"geometry": poly, "cand": k, "sam_iou": np.nan, "solidity": props["solidity"],
                      "elongation": props["elongation"], "core_found": False, "outline": "NDVI patch"})
        n_fallback += 1
    for o in robjs:
        o["source"] = SOURCE_NDVI
        o["cand"] = ("r", o["cand"])
    for o in objs:
        o["cand"] = ("c", o["cand"])
    table = {"c": cand, "r": rcand}
    everything = suppress_overlaps(objs + robjs,
                                   lambda o: (0 if o["source"] == SOURCE_CANDIDATE else 1, -table[o["cand"][0]][o["cand"][1], 2]))

    rows = []
    for o in everything:
        y, x, z, li_, d_, al_, zd_, zn_, onl = table[o["cand"][0]][o["cand"][1]]
        g = o["geometry"]
        if o["source"] == SOURCE_CANDIDATE:
            conf = detection_confidence(o["sam_iou"], float(z))
        else:  # NDVI patch: strength from the patch's NDVI anomaly; 0.5 mask quality without a SAM mask
            q = o["sam_iou"] if np.isfinite(o["sam_iou"]) else 0.5
            conf = float(np.sqrt(q * (1.0 - np.exp(-max(float(zn_) - 3.0, 0.0) / 2.5))))
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
            "sam_iou": round(float(o["sam_iou"]), 3) if np.isfinite(o["sam_iou"]) else None,
            "core_found": bool(o["core_found"]),
            "detection_source": o["source"],
            "outline": o.get("outline", "SAM 2.1 mask"),
            "detection_confidence": round(conf, 3),
            "geometry": g,
        })
    raw = gpd.GeoDataFrame(rows, geometry="geometry", crs=crs).drop(columns=["line_idx", "along_px"])
    raw.to_file(out / "plants_raw.geojson", driver="GeoJSON")
    return finalize(raw, {"n_candidates": int(len(pts)), "n_candidates_on_line": int(on_line.sum()),
                          "n_candidates_kept": int(len(cand)), "n_ndvi_recall_patches": int(len(rcand)),
                          "n_ndvi_recall_sam": int(len(robjs) - n_fallback), "n_ndvi_recall_patch_outline": n_fallback})


def suppress_overlaps(objs, key):
    """Greedy overlap suppression (overlap / smaller area >= 0.3); objects sorted by key, first wins."""
    from shapely.strtree import STRtree
    objs = sorted(objs, key=key)
    if not objs:
        return objs
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
    return [o for i, o in enumerate(objs) if not dropped[i]]


def ndvi_recall_candidates(ndvi, P, objs, transform, lines):
    """NDVI vegetation patches (NDVI above local soil, robust z >= RECALL_Z, area >= RECALL_MIN_AREA_M2)
    not touched by any detected object. Returns candidate rows (same columns as the main
    candidates, ndvi_anomaly_z = patch maximum) and, per row, (patch mask window, row0, col0)."""
    from rasterio.features import rasterize
    z = _robust_z(ndvi - _nan_gauss(ndvi, BG_SIGMA))
    veg = (np.nan_to_num(z, nan=-9) >= RECALL_Z).astype(np.uint8)
    veg = cv2.morphologyEx(veg, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    covered = rasterize(((o["geometry"], 1) for o in objs), out_shape=ndvi.shape, transform=transform,
                        fill=0, dtype="uint8") if objs else np.zeros(ndvi.shape, np.uint8)
    covered = cv2.dilate(covered, np.ones((5, 5), np.uint8))
    n, cc, stats, _ = cv2.connectedComponentsWithStats(veg, connectivity=8)
    hit = np.bincount(cc[covered > 0].ravel(), minlength=n)
    min_px = RECALL_MIN_AREA_ON_LINE_M2 / PIXEL_M ** 2
    rows, masks = [], []
    nd0 = np.nan_to_num(ndvi, nan=-9)
    for i in range(1, n):
        x0, y0, w, h, area = stats[i]
        if area < min_px or hit[i] > 0:
            continue
        win = cc[y0:y0 + h, x0:x0 + w] == i
        sub = np.where(win, nd0[y0:y0 + h, x0:x0 + w], -9)
        py, px = np.unravel_index(np.argmax(sub), sub.shape)
        rows.append((y0 + py, x0 + px, float(np.nan_to_num(P[y0 + py, x0 + px])),
                     float(np.nanmax(np.where(win, z[y0:y0 + h, x0:x0 + w], np.nan)))))
        masks.append((win.astype(np.uint8), y0, x0))
    if not rows:
        return np.zeros((0, 9)), []
    rows = np.array(rows)
    areas = np.array([m[0].sum() for m in masks]) * PIXEL_M ** 2
    li, dist, along = lines.nearest(rows[:, 0], rows[:, 1])
    on = np.abs(dist) <= ON_LINE_PX
    ok = on | (areas >= RECALL_MIN_AREA_M2)       # small patches only count on a planting line
    rows, li, dist, along, on = rows[ok], li[ok], dist[ok], along[ok], on[ok]
    masks = [m for m, k in zip(masks, ok) if k]
    if not len(rows):
        return np.zeros((0, 9)), []
    # columns: row, col, z, line_idx, dist_px, along_px, darkness_z (n/a), ndvi_anomaly_z, on_line
    return np.c_[rows[:, :3], li, dist, along, np.full(len(rows), np.nan), rows[:, 3], on], masks


def merge_planting_positions(gdf):
    """One plant per planting position, chosen by the planting rhythm of each line.

    1. fragments touching each other (<= TOUCH_M) on the same line are joined into one object
    2. per line, dynamic programming picks the subset of objects that best fits a regular
       planting rhythm: consecutive picks must be ~k x spacing apart (k >= 1, deviation <=
       RHYTHM_TOL spacings; k > 1 = skipped / missing plants or a cross lane). Each object
       scores its evidence (detection confidence, vegetation signal, closeness to the drip
       line) minus a penalty for deviating from the rhythm. Objects not picked sit between
       planting positions: kept as 'Between positions' vegetation when vegetated, else dropped.
    The spacing is measured from the data (median gap between neighbouring objects)."""
    import geopandas as gpd
    from shapely.ops import unary_union
    planted = gdf[gdf.on_planting_line].sort_values(["line_idx", "along_px"])
    rest = gdf[~gdf.on_planting_line]
    joined = []
    for _, grp in planted.groupby("line_idx", sort=False):
        recs = list(grp.itertuples(index=False))
        used = [False] * len(recs)
        for i, r in enumerate(recs):
            if used[i]:
                continue
            group = [r]
            used[i] = True
            for j in range(i + 1, len(recs)):
                if (recs[j].along_px - r.along_px) * PIXEL_M > MERGE_M:
                    break
                if not used[j] and any(recs[j].geometry.distance(g.geometry) <= TOUCH_M for g in group):
                    group.append(recs[j])
                    used[j] = True
            joined.append(_join(group, unary_union))
    objs = gpd.GeoDataFrame(joined, geometry="geometry", crs=gdf.crs)
    spacing = _spacing(objs)
    keep_rows, extra_rows = [], []
    for _, grp in objs.groupby("line_idx", sort=False):
        grp = grp.sort_values("along_px")
        sel = _rhythm_select(grp.along_px.values * PIXEL_M, _evidence(grp), spacing)
        for k, (_, r) in enumerate(grp.iterrows()):
            if sel[k]:
                keep_rows.append(r)
            else:
                vegetated = (np.nan_to_num(r.ndvi_anomaly_z, nan=-9) >= OFFLINE_MIN_ZN) or r.detection_source == SOURCE_NDVI
                if vegetated and r.geometry.area >= BETWEEN_MIN_AREA_M2:
                    r = r.copy()
                    r["on_planting_line"] = False
                    r["position_type"] = "Between positions"
                    extra_rows.append(r)
    parts = [gpd.GeoDataFrame(keep_rows, geometry="geometry", crs=gdf.crs), rest.assign(n_parts=1)]
    if extra_rows:
        parts.append(gpd.GeoDataFrame(extra_rows, geometry="geometry", crs=gdf.crs))
    out = gpd.GeoDataFrame(pd_concat(parts), geometry="geometry", crs=gdf.crs)
    out.attrs["spacing_m"] = spacing
    return out


def _join(group, unary_union):
    best = max(group, key=lambda r: r.detection_confidence)
    row = best._asdict()
    if len(group) > 1:
        g = unary_union([r.geometry for r in group]).buffer(0.03).buffer(-0.03)
        if g.geom_type != "Polygon":
            g = max(g.geoms, key=lambda x: x.area) if g.geom_type == "MultiPolygon" else best.geometry
        row["geometry"] = g
        row["area_m2"] = round(g.area, 4)
        row["equiv_diameter_m"] = round(2 * np.sqrt(g.area / np.pi), 3)
        row["perimeter_m"] = round(g.length, 3)
        row["core_found"] = any(r.core_found for r in group)
        row["ndvi_anomaly_z"] = max(r.ndvi_anomaly_z for r in group)
        row["along_px"] = float(np.mean([r.along_px for r in group]))
        row["dist_to_line_m"] = float(np.mean([r.dist_to_line_m for r in group]))
    row["n_parts"] = len(group)
    return row


def _spacing(objs) -> float:
    d = []
    for _, g in objs.groupby("line_idx"):
        d.extend(np.diff(np.sort(g.along_px.values)) * PIXEL_M)
    d = np.array(d)
    core = d[(d > 1.2) & (d < 3.2)]
    return float(np.median(core)) if len(core) else 2.0


def _evidence(grp) -> np.ndarray:
    conf = grp.detection_confidence.values.astype(float)
    veg = np.clip(np.nan_to_num(grp.ndvi_anomaly_z.values.astype(float), nan=0.0), 0, 10) / 10
    near = 1 - np.clip(np.abs(grp.dist_to_line_m.values.astype(float)) / (CENTROID_LINE_PX * PIXEL_M), 0, 1)
    return 1.0 + W_CONF * conf + W_VEG * veg + W_LINE * near


def _rhythm_select(s: np.ndarray, score: np.ndarray, spacing: float) -> np.ndarray:
    """DP over objects sorted along the line; returns a boolean mask of chosen positions."""
    n = len(s)
    if n == 0:
        return np.zeros(0, bool)
    best = score.copy()
    prev = np.full(n, -1)
    for j in range(n):
        for i in range(j):
            d = (s[j] - s[i]) / spacing
            k = round(d)
            if k < 1:
                continue
            dev = abs(d - k)
            if dev > RHYTHM_TOL:
                continue
            v = best[i] + score[j] - RHYTHM_PENALTY * dev
            if v > best[j]:
                best[j], prev[j] = v, i
    sel = np.zeros(n, bool)
    j = int(np.argmax(best))
    while j >= 0:
        sel[j] = True
        j = prev[j]
    # objects before the first / after the last pick that fit no chain are left unselected
    return sel


def pd_concat(frames):
    import pandas as pd
    return pd.concat(frames, ignore_index=True)


def inferred_gaps(gdf, lines, transform):
    """Planting positions with no detected plant, inferred along each line from the local
    spacing: a gap between consecutive planted positions of 1.5-4.5x the median spacing holds
    round(gap / spacing) - 1 missing plants. Longer gaps (cross lanes, line ends, buried line
    stretches) are not filled."""
    import geopandas as gpd
    from shapely.geometry import Point
    pl = gdf[gdf.on_planting_line]
    diffs = []
    for _, g in pl.groupby("line_idx"):
        d = np.diff(np.sort(g.along_px.values)) * PIXEL_M
        diffs.extend(d)
    diffs = np.array(diffs)
    core = diffs[(diffs > 1.0) & (diffs < 3.5)]
    spacing = float(np.median(core)) if len(core) else 2.0
    from scipy.spatial import cKDTree
    others = gdf[~gdf.on_planting_line]
    oc = others.geometry.centroid
    tree = cKDTree(np.c_[oc.x.values, oc.y.values]) if len(others) else None
    promoted = set()
    pts, attrs = [], []
    for li, g in pl.groupby("line_idx"):
        s = np.sort(g.along_px.values) * PIXEL_M
        for a, b in zip(s[:-1], s[1:]):
            gap = b - a
            if 1.5 * spacing < gap <= 4.5 * spacing:
                n = int(round(gap / spacing)) - 1
                for k in range(1, n + 1):
                    yr = (a + k * gap / (n + 1)) / PIXEL_M
                    ys_t, xs_t = lines.tracks[int(li)]
                    xr = np.interp(yr, ys_t, xs_t)
                    row, col = lines.to_pix(np.array([yr]), np.array([xr]))
                    x, y = rasterio_xy(transform, row[0], col[0])
                    # a vegetated object right at the expected spot is the plant for that spot
                    if tree is not None:
                        near = [j for j in tree.query_ball_point([x, y], PROMOTE_M) if others.index[j] not in promoted]
                        if near:
                            j = min(near, key=lambda q: np.hypot(oc.x.values[q] - x, oc.y.values[q] - y))
                            promoted.add(others.index[j])
                            continue
                    pts.append(Point(x, y))
                    attrs.append({"line_id": f"L{int(li) + 1:03d}", "status": "Not detected (inferred)",
                                  "gap_m": round(gap, 2)})
    if promoted:
        idx = list(promoted)
        gdf.loc[idx, "on_planting_line"] = True
        gdf.loc[idx, "position_type"] = "Planting line"
        gdf.loc[idx, "filled_gap"] = True
    out = gpd.GeoDataFrame(attrs, geometry=pts, crs=gdf.crs) if pts else \
        gpd.GeoDataFrame({"line_id": [], "status": [], "gap_m": []}, geometry=[], crs=gdf.crs)
    out.attrs["spacing_m"] = round(spacing, 3)
    out.attrs["promoted"] = len(promoted)
    return out


def pipe_segments(raw, lines, transform) -> np.ndarray:
    """Dark drip-pipe sections (thick or doubled pipe) segment like plants. Reject objects that
    are elongated (>= PIPE_MIN_ELONGATION), aligned with the drip line (within PIPE_MAX_ANGLE
    degrees) and not green (NDVI anomaly z < PIPE_MAX_NDVI_Z)."""
    import rasterio
    inv = ~transform
    flags = np.zeros(len(raw), bool)
    for k, (g, zn) in enumerate(zip(raw.geometry, raw.ndvi_anomaly_z.values)):
        if np.nan_to_num(zn, nan=0.0) >= PIPE_MAX_NDVI_Z:
            continue
        xy = np.asarray(g.exterior.coords)
        cols, rows = inv * (xy[:, 0], xy[:, 1])
        yr, xr = lines.to_rot(np.asarray(rows), np.asarray(cols))
        pts = np.c_[xr - xr.mean(), yr - yr.mean()]
        ev, evec = np.linalg.eigh(np.cov(pts.T))
        elong = np.sqrt(max(ev[1], 1e-9) / max(ev[0], 1e-9))
        angle = np.degrees(np.arctan2(abs(evec[0, 1]), abs(evec[1, 1])))   # major axis vs vertical (= line)
        flags[k] = elong >= PIPE_MIN_ELONGATION and angle <= PIPE_MAX_ANGLE
    return flags


def rasterio_xy(transform, row, col):
    import rasterio
    x, y = rasterio.transform.xy(transform, row, col, offset="center")
    return float(x), float(y)


def finalize(raw, cand_stats: dict) -> dict:
    """Assign each segmented object to a planting line or 'between lines', then IDs.

    Planted position = polygon centroid within CENTROID_LINE_PX of a tracked line AND a
    visible drip line locally (detect_lines.LineModel.visibility >= LINE_VISIBILITY_MIN).
    Rows of pits / vegetation where no drip line is visible are therefore not planting
    lines. Between-line objects are kept only with a vegetation signal (NDVI anomaly z >= 4).
    """
    import geopandas as gpd  # noqa: F401
    import rasterio
    from detect_lines import LineModel

    out = out_dir("detection")
    with rasterio.open(OUT / "aligned" / "ndvi_coreg.tif") as d:
        transform = d.transform
    lines = LineModel.load(OUT / "lines" / "lines_model.npz")
    R = np.load(OUT / "lines" / "line_evidence_rot.npy").astype(np.float32)
    cen = raw.geometry.centroid
    r, c = rasterio.transform.rowcol(transform, cen.x.values, cen.y.values)
    r, c = np.asarray(r, float), np.asarray(c, float)
    li, dist, along = lines.nearest(r, c)
    radius_px = np.sqrt(raw.area.values / np.pi) / PIXEL_M
    vis = lines.visibility(r, c, radius_px, R)
    del R
    on = (np.abs(dist) <= CENTROID_LINE_PX) & (np.nan_to_num(vis, nan=-1) >= LINE_VISIBILITY_MIN)
    src = raw["detection_source"].values if "detection_source" in raw else np.full(len(raw), SOURCE_CANDIDATE)
    vegetated = (raw.ndvi_anomaly_z.values >= OFFLINE_MIN_ZN) | (src == SOURCE_NDVI)
    pipe = pipe_segments(raw, lines, transform)
    keep = (on | (vegetated & (raw.area.values >= BETWEEN_MIN_AREA_M2))) & ~pipe

    gdf = raw.copy()
    gdf["line_idx"], gdf["along_px"] = li, along
    gdf["line_id"] = [f"L{int(i) + 1:03d}" if i >= 0 else None for i in li]
    gdf["dist_to_line_m"] = np.round(dist * PIXEL_M, 3)
    gdf["drip_line_visibility"] = np.round(vis, 2)
    gdf["on_planting_line"] = on
    gdf["position_type"] = np.where(on, "Planting line", "Between lines")
    n_dropped = int((~keep).sum())
    gdf = gdf[keep]
    n_before_merge = int(gdf.on_planting_line.sum())
    gdf = merge_planting_positions(gdf)
    gdf = gdf.sort_values(["on_planting_line", "line_idx", "along_px"], ascending=[False, True, True]).reset_index(drop=True)
    gdf["filled_gap"] = False
    gaps = inferred_gaps(gdf, lines, transform)
    gdf = gdf.sort_values(["on_planting_line", "line_idx", "along_px"], ascending=[False, True, True]).reset_index(drop=True)
    gdf.insert(0, "plant_id", [f"P{i + 1:05d}" for i in range(len(gdf))])
    cen = gdf.geometry.centroid
    gdf["centroid_x"], gdf["centroid_y"] = cen.x.round(3), cen.y.round(3)
    ll = cen.to_crs(4326)
    gdf["lon"], gdf["lat"] = ll.x.round(7), ll.y.round(7)
    gdf["detection_model"] = DETECTION_MODEL
    gdf["detection_model_version"] = DETECTION_VERSION
    gdf["processing_date"] = PROCESSING_DATE
    gdf = gdf.drop(columns=["line_idx", "along_px"])
    gdf.to_file(out / "plants.geojson", driver="GeoJSON")
    gaps.to_file(out / "planting_gaps.geojson", driver="GeoJSON")
    n_planted = int(gdf.on_planting_line.sum())
    positions = {"planted_positions_detected": n_planted,
                 "objects_merged_into_positions": n_before_merge - n_planted,
                 "inferred_missing_positions": int(len(gaps)),
                 "expected_planting_positions": n_planted + int(len(gaps)),
                 "along_line_spacing_m": gaps.attrs.get("spacing_m"),
                 "gaps_filled_by_vegetation": gaps.attrs.get("promoted", 0),
                 "pipe_segments_rejected": int(pipe.sum())}
    cand_stats = {**cand_stats, **positions}

    summary = {
        **cand_stats, "n_segmented": int(len(raw)), "n_plants": int(len(gdf)),
        "n_on_planting_line": int(gdf.on_planting_line.sum()),
        "n_between_lines": int((~gdf.on_planting_line).sum()),
        "n_dropped_no_line_no_vegetation": n_dropped,
        "area_m2_median": float(gdf.area_m2.median()), "area_m2_p90": float(gdf.area_m2.quantile(0.9)),
        "detection_confidence_median": float(gdf.detection_confidence.median()),
        "core_found_fraction": float(gdf.core_found.mean()),
        "by_source": {str(k): int(v) for k, v in gdf["detection_source"].value_counts().items()} if "detection_source" in gdf else {},
        "parameters": {"PEAK_Z": PEAK_Z, "PEAK_WINDOW_px": PEAK_WINDOW, "BG_SIGMA_px": BG_SIGMA,
                       "ON_LINE_PX": ON_LINE_PX, "OFFLINE_MIN_ZN": OFFLINE_MIN_ZN,
                       "CENTROID_LINE_PX": CENTROID_LINE_PX, "RECALL_Z": RECALL_Z,
                       "RECALL_MIN_AREA_M2": RECALL_MIN_AREA_M2,
                       "RECALL_MIN_AREA_ON_LINE_M2": RECALL_MIN_AREA_ON_LINE_M2, "LINE_VISIBILITY_MIN": LINE_VISIBILITY_MIN,
                       "area_m2": [MIN_AREA_M2, MAX_AREA_M2], "between_min_area_m2": BETWEEN_MIN_AREA_M2,
                       "sam_scale": SAM_SCALE, "min_solidity": MIN_SOLIDITY,
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
        s = min(s, d.width, d.height)            # small surveys: fit the window to the raster
        if c0 + s > d.width or r0 + s > d.height:
            c0, r0 = (d.width - s) // 2, (d.height - s) // 2
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
    import sys
    if "--positions-only" in sys.argv:
        import geopandas as gpd
        from common import read_json
        prev = read_json(OUT / "detection" / "detection_summary.json")
        finalize(gpd.read_file(OUT / "detection" / "plants_raw.geojson"),
                 {k: prev[k] for k in ("n_candidates", "n_candidates_on_line", "n_candidates_kept") if k in prev})
    else:
        main()
