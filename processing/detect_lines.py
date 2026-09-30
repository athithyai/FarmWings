"""Stage 2a - detect the drip-irrigation planting lines.

The planted seedlings sit in pits along thin dark drip lines. Only the visible drip lines
are planting lines (rows of pits / vegetation without a drip line are not). They are
the planting reference: a detection on a line is at a planted position, a detection
between lines is spontaneous vegetation or a soil feature.

Method
  1. black top-hat of brightness (grey closing 7 px - brightness) -> thin dark features
  2. dominant orientation by maximising column-profile variance over rotations
  3. rotate so lines run vertically; in overlapping 400 px (9.4 m) blocks, peaks of the
     column profile are line positions (tolerates gentle curvature / non-parallel lines)
  4. link block peaks into tracks (|dx| < 20 px), drop tracks seen in < 3 blocks and
     short tracks lying < 60 px from a long line (spurious in-between peaks);
     interpolate each track x'(y') for distance queries

Outputs (processing_outputs/lines/): planting_lines.geojson, lines_model.npz, lines_summary.json,
         line_evidence_rot.npy (top-hat in the line frame, used for per-plant line visibility)
"""
from __future__ import annotations

from common import OUT, PROCESSING_DATE, out_dir, write_json  # noqa: I001 (sets PROJ_DATA first)

import cv2
import geopandas as gpd
import numpy as np
import rasterio
from scipy.signal import find_peaks
from shapely.geometry import LineString

KERNEL = 7
BLOCK, BLOCK_STEP = 400, 200
MIN_PEAK_SEP = 50          # px (1.2 m) - lines are ~85 px apart
LINK_TOL = 20              # px
MIN_TRACK_BLOCKS = 3
VIS_REACH_PX = 80          # px (1.9 m) - how far along the line to look for local evidence
VIS_HALF_PX = 6            # px - half width of the line / soil windows (lines curve slightly)
VIS_MIN = 5.0              # visibility score above which a drip line counts as visible


def tophat(rgb: np.ndarray, valid: np.ndarray) -> np.ndarray:
    L = rgb[:3].astype(np.float32).mean(0)
    T = cv2.morphologyEx(L, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (KERNEL, KERNEL))) - L
    T = np.where(valid, T, 0)
    return np.minimum(T, np.percentile(T[valid], 99.5))


def dominant_angle(T: np.ndarray) -> float:
    H, W = T.shape
    s = 3000
    crop = T[H // 2 - s // 2:H // 2 + s // 2, W // 2 - s // 2:W // 2 + s // 2]

    def score(a):
        M = cv2.getRotationMatrix2D((s / 2, s / 2), a, 1)
        R = cv2.warpAffine(crop, M, (s, s))
        return R[700:2300, 700:2300].mean(0).var()

    coarse = np.arange(-90, 90, 1.0)
    best = coarse[int(np.argmax([score(a) for a in coarse]))]
    fine = np.arange(best - 1.5, best + 1.5, 0.05)
    return float(fine[int(np.argmax([score(a) for a in fine]))])


class LineModel:
    """Rotation into the line frame + tracked line positions; answers distance queries."""

    def __init__(self, M, tracks):
        self.M = M                        # 2x3 affine: (col,row) -> (x', y')
        self.Minv = cv2.invertAffineTransform(M)
        self.tracks = tracks              # list of (ys, xs) arrays in rotated frame

    def to_rot(self, rows, cols):
        p = np.c_[cols, rows, np.ones(len(rows))] @ self.M.T
        return p[:, 1], p[:, 0]           # y', x'

    def to_pix(self, yr, xr):
        p = np.c_[xr, yr, np.ones(len(yr))] @ self.Minv.T
        return p[:, 1], p[:, 0]           # row, col

    def nearest(self, rows, cols, margin=BLOCK):
        """For each pixel point -> (line index or -1, signed across-line distance px, along-line y')."""
        yr, xr = self.to_rot(np.asarray(rows, float), np.asarray(cols, float))
        best_d = np.full(len(yr), np.inf)
        best_i = np.full(len(yr), -1)
        for i, (ys, xs) in enumerate(self.tracks):
            inside = (yr >= ys[0] - margin) & (yr <= ys[-1] + margin)
            if not inside.any():
                continue
            d = xr - np.interp(yr, ys, xs)
            upd = inside & (np.abs(d) < np.abs(best_d))
            best_d[upd], best_i[upd] = d[upd], i
        return best_i, best_d, yr

    def visibility(self, rows, cols, radius_px, R, reach=VIS_REACH_PX, half=VIS_HALF_PX):
        """Local drip-line evidence at each point.

        Along the nearest tracked line, within +/- reach px but outside the plant's own crown,
        take per row the max top-hat response in a (2*half+1) px window on the line, and the
        same statistic in equally wide soil windows either side (offset 2*half+4 px). Score =
        median(line row-max) - median(soil row-max). A continuous drip line gives a consistent
        row maximum; soil specks do not. R is the top-hat image in the rotated frame."""
        idx, _, yr = self.nearest(rows, cols)
        H, W = R.shape
        out = np.full(len(yr), np.nan)
        for k, (i, y) in enumerate(zip(idx, yr)):
            if i < 0:
                continue
            ys_t, xs_t = self.tracks[i]
            xl = int(round(np.interp(y, ys_t, xs_t)))
            yy = np.arange(int(y) - reach, int(y) + reach + 1)
            yy = yy[(np.abs(yy - y) > radius_px[k] + 8) & (yy >= 0) & (yy < H)]
            off = 2 * half + 4
            if len(yy) < 20 or xl - off - half < 0 or xl + off + half + 1 >= W:
                continue
            strip = R[yy]
            line = strip[:, xl - half:xl + half + 1].max(1)
            left = strip[:, xl - off - half:xl - off + half + 1].max(1)
            right = strip[:, xl + off - half:xl + off + half + 1].max(1)
            out[k] = float(np.median(line) - np.median(np.r_[left, right]))
        return out

    def save(self, path):
        np.savez(path, M=self.M, n=len(self.tracks),
                 **{f"ys{i}": t[0] for i, t in enumerate(self.tracks)},
                 **{f"xs{i}": t[1] for i, t in enumerate(self.tracks)})

    @classmethod
    def load(cls, path):
        z = np.load(path)
        return cls(z["M"], [(z[f"ys{i}"], z[f"xs{i}"]) for i in range(int(z["n"]))])


def fit_lines(T: np.ndarray, valid: np.ndarray, angle: float):
    """Returns (LineModel, R) where R is the opened top-hat in the rotated frame."""
    H, W = T.shape
    c, s = abs(np.cos(np.radians(angle))), abs(np.sin(np.radians(angle)))
    Wr, Hr = int(W * c + H * s) + 2, int(W * s + H * c) + 2
    M = cv2.getRotationMatrix2D((W / 2, H / 2), angle, 1)
    M[0, 2] += Wr / 2 - W / 2
    M[1, 2] += Hr / 2 - H / 2
    R = cv2.warpAffine(T, M, (Wr, Hr))
    V = cv2.warpAffine(valid.astype(np.uint8), M, (Wr, Hr), flags=cv2.INTER_NEAREST)

    detections = []                                   # (y_centre, x_peak, prominence)
    for y0 in range(0, Hr - BLOCK, BLOCK_STEP):
        blk, vb = R[y0:y0 + BLOCK], V[y0:y0 + BLOCK]
        cnt = vb.sum(0)
        prof = np.where(cnt > 0.6 * BLOCK, blk.sum(0) / np.maximum(cnt, 1), np.nan)
        ok = np.isfinite(prof)
        if ok.sum() < 200:
            continue
        p = np.where(ok, prof, np.nanmedian(prof))
        p = cv2.GaussianBlur(p[None].astype(np.float32), (0, 0), 1.0)[0]
        base = cv2.GaussianBlur(p[None], (0, 0), 25)[0]
        resid = p - base
        mad = np.median(np.abs(resid[ok] - np.median(resid[ok]))) * 1.4826 + 1e-6
        pk, pr = find_peaks(resid, distance=MIN_PEAK_SEP, prominence=3 * mad)
        pk = pk[ok[pk]]
        for x in pk:
            detections.append((y0 + BLOCK / 2, float(x)))

    # Link peaks into tracks, block by block
    detections.sort()
    tracks: list[list[tuple[float, float]]] = []
    by_y: dict[float, list[float]] = {}
    for y, x in detections:
        by_y.setdefault(y, []).append(x)
    for y in sorted(by_y):
        for x in by_y[y]:
            cand = [t for t in tracks if y - t[-1][0] <= 3 * BLOCK_STEP and abs(t[-1][1] - x) < LINK_TOL]
            if cand:
                min(cand, key=lambda t: abs(t[-1][1] - x)).append((y, x))
            else:
                tracks.append([(y, x)])
    out = []
    for t in tracks:
        if len(t) >= MIN_TRACK_BLOCKS:
            ys, xs = np.array(t).T
            out.append((ys, xs))
    # Short tracks squeezed between two real lines (closer than 0.7x spacing) are spurious
    long_ = [t for t in out if len(t[0]) >= 5]
    keep = list(long_)
    for ys, xs in out:
        if len(ys) >= 5:
            continue
        near = [np.min(np.abs(np.interp(ys, ly, lx) - xs)) for ly, lx in long_
                if ly[0] - BLOCK <= ys.mean() <= ly[-1] + BLOCK]
        if not near or min(near) > 60:
            keep.append((ys, xs))
    keep.sort(key=lambda t: np.median(t[1]))
    return LineModel(M, keep), R


def main() -> dict:
    out = out_dir("lines")
    with rasterio.open(OUT / "aligned" / "rgb_aligned.tif") as d:
        rgb = d.read()
        transform, crs = d.transform, d.crs
    valid = rgb[3] > 0
    T = tophat(rgb, valid)
    del rgb
    angle = dominant_angle(T)
    model, R = fit_lines(T, valid, angle)
    model.save(out / "lines_model.npz")
    np.save(out / "line_evidence_rot.npy", R.astype(np.float16))

    # Export only the stretches where the drip line is actually visible (sampled every
    # 20 px = 0.47 m with the same local visibility test used for plants)
    geoms, attrs = [], []
    for i, (ys, xs) in enumerate(model.tracks):
        yy = np.arange(ys[0], ys[-1] + 1, 20.0)
        xx = np.interp(yy, ys, xs)
        rows, cols = model.to_pix(yy, xx)
        vis = model.visibility(rows, cols, np.full(len(yy), -9.0), R, reach=40)
        ok = np.nan_to_num(vis, nan=-1) >= VIS_MIN
        run_start = None
        for k in range(len(yy) + 1):
            if k < len(yy) and ok[k]:
                run_start = k if run_start is None else run_start
                continue
            if run_start is not None and k - run_start >= 3:
                x, y = rasterio.transform.xy(transform, rows[run_start:k], cols[run_start:k], offset="center")
                geoms.append(LineString(np.c_[x, y]))
                attrs.append({"line_id": f"L{i + 1:03d}"})
            run_start = None
    gdf = gpd.GeoDataFrame(attrs, geometry=geoms, crs=crs)
    gdf["length_m"] = gdf.length.round(2)
    gdf["source"] = "drip-line detection (black top-hat + rotated column profiles), visible stretches only"
    gdf["processing_date"] = PROCESSING_DATE
    gdf.to_file(out / "planting_lines.geojson", driver="GeoJSON")

    # Median spacing between neighbouring lines, measured at common y'
    gaps = []
    for (y1, x1), (y2, x2) in zip(model.tracks[:-1], model.tracks[1:]):
        lo, hi = max(y1[0], y2[0]), min(y1[-1], y2[-1])
        if hi > lo:
            yy = np.linspace(lo, hi, 10)
            gaps.append(np.median(np.interp(yy, y2, x2) - np.interp(yy, y1, x1)))
    gaps = np.array(gaps)
    summary = {
        "angle_deg_rotation_to_vertical": angle,
        "n_lines": len(model.tracks),
        "total_length_m_visible": float(gdf.length.sum()),
        "median_line_spacing_m": float(np.median(gaps[(gaps > 40) & (gaps < 140)]) * 0.0236) if len(gaps) else None,
        "method": __doc__.split("Method")[1].split("Outputs")[0].strip(),
    }
    write_json(out / "lines_summary.json", summary)
    print({k: v for k, v in summary.items() if k != "method"})
    return summary


if __name__ == "__main__":
    main()
