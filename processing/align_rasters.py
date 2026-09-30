"""Stage 1 - put RGB and NDVI on one common analysis grid (originals untouched).

The two rasters share CRS (EPSG:32638) and upper-left origin but differ in pixel size
(RGB 6 mm, NDVI 23.6 mm). The analysis grid is the NDVI grid: the RGB is block-averaged
onto it (Resampling.average, alpha band kept as a validity mask). NDVI is copied onto
the same grid with an explicit nodata value.

Outputs (processing_outputs/aligned/):
  rgb_aligned.tif   uint8 RGBA, NDVI grid, tiled + LZW + overviews
  ndvi_aligned.tif  float32, NDVI grid, nodata -32767, tiled + deflate + overviews
  ndvi_coreg.tif    NDVI after local sub-pixel co-registration to the RGB content
  shift_field.json  per-tile shifts measured by phase correlation
  alignment.json    grid definition + residual offset check

Identical georeferencing does not guarantee identical content: phase correlation of NDVI
against RGB darkness shows a spatially varying 1-3 px (2-7 cm) residual offset, so a
smooth shift field is estimated on 1024 px tiles and applied to NDVI (bilinear remap).
"""
from __future__ import annotations

import argparse
from pathlib import Path

from common import DEFAULT_INPUT, find_inputs, out_dir, write_json  # noqa: I001 (sets PROJ_DATA first)

import cv2
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject

NODATA = -32767.0
TILE, STEP, MIN_RESPONSE = 1024, 512, 0.3


def shift_field(rgb: np.ndarray, ndvi: np.ndarray, valid: np.ndarray):
    """Phase-correlate NDVI against RGB darkness per tile. Returns tile centres + shifts (px)."""
    hann = cv2.createHanningWindow((TILE, TILE), cv2.CV_32F)
    H, W = ndvi.shape
    rows = []
    for y in range(0, H - TILE + 1, STEP):
        for x in range(0, W - TILE + 1, STEP):
            if valid[y:y + TILE, x:x + TILE].mean() < 0.97:
                continue
            f = -rgb[:3, y:y + TILE, x:x + TILE].astype(np.float32).sum(0)
            v = ndvi[y:y + TILE, x:x + TILE]
            f = (f - f.mean()) / (f.std() + 1e-6)
            v = (v - v.mean()) / (v.std() + 1e-6)
            (dx, dy), resp = cv2.phaseCorrelate(v.astype(np.float32), f.astype(np.float32), hann)
            if resp >= MIN_RESPONSE and abs(dx) < 8 and abs(dy) < 8:
                rows.append((y + TILE / 2, x + TILE / 2, dy, dx, resp))
    return np.array(rows)


def interpolate_field(rows: np.ndarray, shape, coarse=64):
    """Inverse-distance interpolation of tile shifts onto a coarse grid, then bilinear upsampling."""
    H, W = shape
    gy, gx = np.mgrid[0:H:coarse, 0:W:coarse]
    pts = rows[:, :2]
    d2 = (gy.ravel()[:, None] - pts[None, :, 0]) ** 2 + (gx.ravel()[:, None] - pts[None, :, 1]) ** 2
    w = 1.0 / (d2 + 256.0 ** 2) ** 1.5
    w /= w.sum(1, keepdims=True)
    fy = (w @ rows[:, 2]).reshape(gy.shape).astype(np.float32)
    fx = (w @ rows[:, 3]).reshape(gy.shape).astype(np.float32)
    fy = cv2.resize(fy, (W, H), interpolation=cv2.INTER_LINEAR)
    fx = cv2.resize(fx, (W, H), interpolation=cv2.INTER_LINEAR)
    return fy, fx


def main(input_dir: Path) -> dict:
    rgb_path, ndvi_path = find_inputs(input_dir)
    out = out_dir("aligned")
    with rasterio.open(ndvi_path) as n:
        grid = dict(crs=n.crs, transform=n.transform, width=n.width, height=n.height)
        ndvi = n.read(1)
        src_nodata = n.nodata
    ndvi = np.where((ndvi == src_nodata) | ~np.isfinite(ndvi), NODATA, ndvi).astype("float32")

    rgb = np.zeros((4, grid["height"], grid["width"]), dtype="uint8")
    with rasterio.open(rgb_path) as r:
        for b in range(1, 5):
            reproject(
                source=rasterio.band(r, b), destination=rgb[b - 1],
                src_transform=r.transform, src_crs=r.crs,
                dst_transform=grid["transform"], dst_crs=grid["crs"],
                resampling=Resampling.average, num_threads=4,
            )
            print(f"  RGB band {b} resampled")
        rgb_res = r.res
    # Pixel is valid for analysis when both sources have data
    valid = (rgb[3] > 127) & (ndvi != NODATA)
    rgb[3] = np.where(valid, 255, 0)
    ndvi = np.where(valid, ndvi, NODATA)

    # Local co-registration of NDVI to the RGB content
    rows = shift_field(rgb, np.where(valid, ndvi, np.nan).astype(np.float32), valid)
    fy, fx = interpolate_field(rows, ndvi.shape)
    yy, xx = np.mgrid[0:ndvi.shape[0], 0:ndvi.shape[1]].astype(np.float32)
    # corrected[y, x] = ndvi[y - dy, x - dx]
    src = np.where(valid, ndvi, np.nan).astype(np.float32)
    coreg = cv2.remap(src, xx - fx, yy - fy, interpolation=cv2.INTER_LINEAR,
                      borderMode=cv2.BORDER_CONSTANT, borderValue=np.nan)
    del yy, xx
    coreg = np.where(np.isfinite(coreg) & valid, coreg, NODATA).astype("float32")
    write_json(out / "shift_field.json", {
        "tile_px": TILE, "step_px": STEP, "min_response": MIN_RESPONSE,
        "columns": ["row_centre", "col_centre", "dy_px", "dx_px", "response"],
        "rows": rows.round(3).tolist()})

    base = dict(driver="GTiff", tiled=True, blockxsize=512, blockysize=512, **grid)
    with rasterio.open(out / "rgb_aligned.tif", "w", count=4, dtype="uint8", compress="lzw",
                       photometric="RGB", alpha="YES", **base) as d:
        d.write(rgb)
        d.build_overviews([2, 4, 8, 16, 32], Resampling.average)
    with rasterio.open(out / "ndvi_aligned.tif", "w", count=1, dtype="float32", nodata=NODATA,
                       compress="deflate", predictor=3, **base) as d:
        d.write(ndvi, 1)
        d.build_overviews([2, 4, 8, 16, 32], Resampling.average)
    with rasterio.open(out / "ndvi_coreg.tif", "w", count=1, dtype="float32", nodata=NODATA,
                       compress="deflate", predictor=3, **base) as d:
        d.write(coreg, 1)
        d.build_overviews([2, 4, 8, 16, 32], Resampling.average)

    # Residual check: correlation of RGB darkness with NDVI before / after (every 3rd pixel)
    def corr(a):
        f = -rgb[:3].astype(np.float32).sum(0)
        m = valid & (a != NODATA)
        sub = (slice(None, None, 3), slice(None, None, 3))
        return float(np.corrcoef(f[sub][m[sub]], a[sub][m[sub]])[0, 1])

    info = {
        "analysis_grid": "NDVI native grid",
        "pixel_size_m": abs(grid["transform"].a),
        "width": grid["width"], "height": grid["height"],
        "crs": grid["crs"].to_string(), "transform": list(grid["transform"])[:6],
        "rgb_source_pixel_size_m": list(rgb_res),
        "rgb_resampling": "average (block mean of ~3.93 x 3.93 source pixels)",
        "valid_pixels": int(valid.sum()),
        "valid_area_m2": float(valid.sum() * abs(grid["transform"].a * grid["transform"].e)),
        "coregistration": {
            "method": "phase correlation of NDVI vs RGB darkness on 1024 px tiles (step 512), IDW-smoothed shift field, bilinear remap",
            "tiles_used": int(len(rows)),
            "shift_px_median_dy_dx": [float(np.median(rows[:, 2])), float(np.median(rows[:, 3]))],
            "shift_px_abs_max": float(np.abs(rows[:, 2:4]).max()),
            "shift_m_median_magnitude": float(np.median(np.hypot(rows[:, 2], rows[:, 3])) * abs(grid["transform"].a)),
            "corr_rgbdark_ndvi_before": corr(ndvi),
            "corr_rgbdark_ndvi_after": corr(coreg),
        },
    }
    write_json(out / "alignment.json", info)
    print(info)
    return info


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    main(ap.parse_args().input)
