"""Stage 0 - inspect the source rasters (read-only) and write an inventory.

Outputs: processing_outputs/inspect/inventory.json, rgb_preview.png, ndvi_preview.png
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from common import DEFAULT_INPUT, find_inputs, out_dir, write_json  # noqa: I001 (sets PROJ_DATA first)

import numpy as np
import rasterio
from rasterio.enums import Resampling
from PIL import Image

PREVIEW_MAX = 2048


def describe(path: Path) -> dict:
    with rasterio.open(path) as d:
        info = {
            "file": path.name,
            "size_bytes": path.stat().st_size,
            "driver": d.driver,
            "dtype": d.dtypes[0],
            "count": d.count,
            "width": d.width,
            "height": d.height,
            "crs": d.crs.to_string(),
            "epsg": d.crs.to_epsg(),
            "transform": list(d.transform)[:6],
            "pixel_size_m": [abs(d.res[0]), abs(d.res[1])],
            "bounds": list(d.bounds),
            "extent_m": [d.bounds.right - d.bounds.left, d.bounds.top - d.bounds.bottom],
            "nodata": d.nodata,
            "colorinterp": [c.name for c in d.colorinterp],
            "compression": d.profile.get("compress"),
            "tiled": d.profile.get("tiled"),
            "block_shape": list(d.block_shapes[0]),
            "overviews": d.overviews(1),
            "tags": d.tags(),
            "sidecars": sorted(p.name for p in path.parent.glob(path.stem + ".*") if p != path),
        }
    return info


def decimated(path: Path, bands=None):
    with rasterio.open(path) as d:
        scale = max(d.width, d.height) / PREVIEW_MAX
        shape = (int(d.height / scale), int(d.width / scale))
        idx = bands or list(range(1, d.count + 1))
        arr = d.read(idx, out_shape=(len(idx), *shape), resampling=Resampling.average, masked=False)
        return arr, scale


def main(input_dir: Path) -> dict:
    rgb_path, ndvi_path = find_inputs(input_dir)
    out = out_dir("inspect")
    rgb, ndvi = describe(rgb_path), describe(ndvi_path)

    # NDVI value distribution (full-resolution read, ~370 MB float32)
    with rasterio.open(ndvi_path) as d:
        a = d.read(1, masked=True)
    valid = a.compressed()
    q = np.percentile(valid, [0, 1, 5, 25, 50, 75, 95, 99, 100])
    hist, edges = np.histogram(valid, bins=40, range=(-1, 1))
    ndvi["value_stats"] = {
        "valid_pixels": int(valid.size),
        "nodata_pixels": int(a.mask.sum()),
        "valid_fraction": float(valid.size / a.size),
        "min": float(q[0]), "p1": float(q[1]), "p5": float(q[2]), "p25": float(q[3]),
        "median": float(q[4]), "p75": float(q[5]), "p95": float(q[6]), "p99": float(q[7]), "max": float(q[8]),
        "mean": float(valid.mean()), "std": float(valid.std()),
        "outside_minus1_1": int(((valid < -1) | (valid > 1)).sum()),
        "fraction_above_0.2": float((valid > 0.2).mean()),
        "fraction_above_0.3": float((valid > 0.3).mean()),
        "histogram": {"edges": edges.round(3).tolist(), "counts": hist.tolist()},
    }
    del a, valid

    # RGB: alpha band coverage + band stats from a decimated read
    arr, scale = decimated(rgb_path)
    alpha = arr[3] if arr.shape[0] == 4 else np.full(arr.shape[1:], 255)
    inside = alpha > 0
    rgb["value_stats_decimated"] = {
        "decimation_factor": round(scale, 2),
        "alpha_valid_fraction": float(inside.mean()),
        "band_means_inside": [float(arr[i][inside].mean()) for i in range(3)],
        "band_std_inside": [float(arr[i][inside].std()) for i in range(3)],
    }
    Image.fromarray(np.dstack([arr[0], arr[1], arr[2], alpha]).astype(np.uint8)).save(out / "rgb_preview.png")

    n, _ = decimated(ndvi_path)
    n = n[0]
    n = np.where(n <= -1.5, np.nan, n)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.imsave(out / "ndvi_preview.png", n, cmap="RdYlGn", vmin=-0.2, vmax=0.8)

    # Alignment check
    align = {
        "same_crs": rgb["crs"] == ndvi["crs"],
        "origin_offset_m": [ndvi["transform"][2] - rgb["transform"][2], ndvi["transform"][5] - rgb["transform"][5]],
        "extent_difference_m": [ndvi["extent_m"][0] - rgb["extent_m"][0], ndvi["extent_m"][1] - rgb["extent_m"][1]],
        "resolution_ratio": ndvi["pixel_size_m"][0] / rgb["pixel_size_m"][0],
        "same_grid": rgb["transform"] == ndvi["transform"] and rgb["width"] == ndvi["width"],
    }
    inv = {"input_dir": Path(input_dir).name, "rgb": rgb, "ndvi": ndvi, "alignment": align}
    write_json(out / "inventory.json", inv)
    print(f"RGB  {rgb['width']}x{rgb['height']} @ {rgb['pixel_size_m'][0]:.4f} m  {rgb['crs']}")
    print(f"NDVI {ndvi['width']}x{ndvi['height']} @ {ndvi['pixel_size_m'][0]:.4f} m  range {ndvi['value_stats']['min']:.3f}..{ndvi['value_stats']['max']:.3f}")
    print("alignment", align)
    return inv


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    main(ap.parse_args().input)
