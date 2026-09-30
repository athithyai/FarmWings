"""Per-plant zonal features on the aligned 2.36 cm grid, plus 6 mm RGB crops.

Shared by the identification and health stages (each stage computes what it needs; no
stage reads another model's outputs).
"""
from __future__ import annotations

from common import OUT  # noqa: I001 (sets PROJ_DATA first)

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.windows import Window

EPS = 1e-6


def plant_pixels(gdf, geoms=None, exclude_geoms=None):
    """Rasterise polygons onto the aligned grid; returns (ndvi_list, rgb_list) of per-plant pixel arrays.

    geoms: alternative geometries per plant (e.g. background rings), default the plant polygons.
    exclude_geoms: pixels covered by these are dropped (e.g. all plant crowns, for rings).
    """
    with rasterio.open(OUT / "aligned" / "ndvi_coreg.tif") as d:
        ndvi = d.read(1)
        transform, shape = d.transform, (d.height, d.width)
    with rasterio.open(OUT / "aligned" / "rgb_aligned.tif") as d:
        rgb = d.read([1, 2, 3])
    geoms = gdf.geometry if geoms is None else geoms
    lab = rasterize(((g, i + 1) for i, g in enumerate(geoms)), out_shape=shape,
                    transform=transform, fill=0, dtype="int32", all_touched=False)
    if exclude_geoms is not None:
        excl = rasterize(((g, 1) for g in exclude_geoms), out_shape=shape, transform=transform,
                         fill=0, dtype="uint8")
        lab[excl > 0] = 0
    flat = lab.ravel()
    idx = np.flatnonzero(flat)
    order = np.argsort(flat[idx], kind="stable")
    idx = idx[order]
    labels = flat[idx]
    bounds = np.searchsorted(labels, np.arange(1, len(gdf) + 2))
    nd_flat = ndvi.ravel()
    rgb_flat = rgb.reshape(3, -1)
    nds, rgbs = [], []
    for i in range(len(gdf)):
        pix = idx[bounds[i]:bounds[i + 1]]
        v = nd_flat[pix]
        ok = v > -2
        nds.append(v[ok])
        rgbs.append(rgb_flat[:, pix[ok]].astype(np.float32).T)
    return nds, rgbs


def ndvi_stats(v: np.ndarray) -> dict:
    if v.size == 0:
        return {k: None for k in ["mean_ndvi", "median_ndvi", "min_ndvi", "max_ndvi", "std_ndvi",
                                  "p10_ndvi", "p90_ndvi"]} | {"valid_pixel_count": 0}
    p = np.percentile(v, [10, 50, 90])
    return {"mean_ndvi": float(v.mean()), "median_ndvi": float(p[1]), "min_ndvi": float(v.min()),
            "max_ndvi": float(v.max()), "std_ndvi": float(v.std()), "p10_ndvi": float(p[0]),
            "p90_ndvi": float(p[2]), "valid_pixel_count": int(v.size)}


def rgb_indices(px: np.ndarray) -> dict:
    """Colour indices from per-pixel RGB (0-255). Chromatic coordinates are used so that
    brightness differences (sun / soil) cancel out."""
    if len(px) == 0:
        return {k: None for k in ["exg", "vari", "gli", "rg_ratio", "green_fraction", "brightness", "hue_deg"]}
    R, G, B = px[:, 0], px[:, 1], px[:, 2]
    S = R + G + B + EPS
    r, g, b = R / S, G / S, B / S
    exg = 2 * g - r - b
    vari = (G - R) / (G + R - B + EPS)
    vari = np.clip(vari, -1, 1)
    gli = (2 * G - R - B) / (2 * G + R + B + EPS)
    mr, mg, mb = R.mean(), G.mean(), B.mean()
    # hue of the mean colour
    mx, mn = max(mr, mg, mb), min(mr, mg, mb)
    if mx - mn < EPS:
        hue = 0.0
    elif mx == mr:
        hue = (60 * ((mg - mb) / (mx - mn)) + 360) % 360
    elif mx == mg:
        hue = 60 * ((mb - mr) / (mx - mn)) + 120
    else:
        hue = 60 * ((mr - mg) / (mx - mn)) + 240
    return {"exg": float(exg.mean()), "vari": float(np.median(vari)), "gli": float(gli.mean()),
            "rg_ratio": float(mr / (mg + EPS)), "green_fraction": float((exg > 0.02).mean()),
            "brightness": float(S.mean() / 3), "hue_deg": float(hue)}


def extract_crops(gdf, rgb_path, size_m=1.2, out_px=224, band_rows=2048):
    """Fixed-size (size_m) RGB crops from the ORIGINAL 6 mm orthomosaic, centred on each plant.

    The source is strip-organised (32-row strips), so plants are processed in row bands
    and each band is decoded once.
    """
    import cv2
    with rasterio.open(rgb_path) as d:
        res = abs(d.transform.a)
        half = int(round(size_m / res / 2))
        rows, cols = rasterio.transform.rowcol(d.transform, gdf.centroid_x.values, gdf.centroid_y.values)
        rows, cols = np.asarray(rows), np.asarray(cols)
        crops = np.zeros((len(gdf), out_px, out_px, 3), np.uint8)
        order = np.argsort(rows)
        start = 0
        while start < len(order):
            r0 = max(rows[order[start]] - half, 0)
            r1 = min(r0 + band_rows, d.height)
            members = [k for k in order[start:] if rows[k] + half <= r1 or r1 == d.height]
            members = [k for k in members if rows[k] - half >= r0 or r0 == 0]
            if not members:
                band_rows *= 2
                continue
            band = d.read([1, 2, 3], window=Window(0, r0, d.width, r1 - r0))
            for k in members:
                y, x = rows[k] - r0, cols[k]
                y0, x0 = max(y - half, 0), max(x - half, 0)
                patch = band[:, y0:y + half, x0:x + half]
                patch = np.moveaxis(patch, 0, -1)
                canvas = np.zeros((2 * half, 2 * half, 3), np.uint8)
                canvas[(y0 - (y - half)):(y0 - (y - half)) + patch.shape[0],
                       (x0 - (x - half)):(x0 - (x - half)) + patch.shape[1]] = patch
                crops[k] = cv2.resize(canvas, (out_px, out_px), interpolation=cv2.INTER_AREA)
            done = set(members)
            order = np.array([k for k in order[start:] if k not in done])
            start = 0
            print(f"  crops: band rows {r0}-{r1}, {len(done)} plants, {len(order)} left")
    return crops
