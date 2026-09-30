"""Stage 6 - web imagery: XYZ (Web Mercator) WebP tiles, packed into spatial chunk files.

Layers
  rgb      original 6 mm orthomosaic, averaged to z23 (~1.6 cm ground) and pyramided to z16
  ndvi     co-registered NDVI, colour ramp RdYlGn over [-0.05, 0.55], z16-z22
  vegmask  pixels whose NDVI exceeds the local soil background (robust z >= 3), z16-z22

Why atlases: static hosts that cap file counts or file types (and git with thousands of tiny
files). Tiles of one zoom are grouped into 8 x 8-tile chunks, each written as ONE ordinary
WebP image (2048 x 2048 atlas, tile (x, y) at column x % 8, row y % 8).
public/data/tiles/index.json maps "z/x/y" -> [atlas file, column, row]; the viewer decodes
an atlas once and crops tiles from it through a MapLibre custom protocol.
"""
from __future__ import annotations

import io
import json
import math

from common import DEFAULT_INPUT, OUT, WEB_DATA, find_inputs  # noqa: I001 (sets PROJ_DATA first)

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.warp import reproject, transform_bounds

ORIGIN = math.pi * 6378137.0
CHUNK_SHIFT = 3
NDVI_RANGE = (-0.05, 0.55)
TILES_DIR = WEB_DATA / "tiles"


def res_at(z):
    return 2 * ORIGIN / (256 * 2 ** z)


def target_grid(bounds_3857, z):
    r = res_at(z)
    minx, miny, maxx, maxy = bounds_3857
    tx0, tx1 = int((minx + ORIGIN) // (256 * r)), int((maxx + ORIGIN) // (256 * r))
    ty0, ty1 = int((ORIGIN - maxy) // (256 * r)), int((ORIGIN - miny) // (256 * r))
    W, H = (tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256
    tr = from_origin(-ORIGIN + tx0 * 256 * r, ORIGIN - ty0 * 256 * r, r, r)
    return tx0, ty0, W, H, tr


class Packer:
    def __init__(self, name):
        self.name = name
        self.chunks: dict[tuple, list] = {}

    def add(self, z, x, y, rgba: np.ndarray):
        self.chunks.setdefault((z, x >> CHUNK_SHIFT, y >> CHUNK_SHIFT), []).append((z, x, y, rgba))

    def write(self, minzoom, maxzoom, bounds_ll):
        d = TILES_DIR / self.name
        d.mkdir(parents=True, exist_ok=True)
        for old in list(d.glob("*.bin")) + list(d.glob("*.webp")):
            old.unlink()
        n = 1 << CHUNK_SHIFT
        files, tiles, total = [], {}, 0
        for key in sorted(self.chunks):
            fn = f"{self.name}/z{key[0]}_{key[1]}_{key[2]}.webp"
            atlas = np.zeros((n * 256, n * 256, 4), np.uint8)
            for z, x, y, rgba in self.chunks[key]:
                col, row = x & (n - 1), y & (n - 1)
                atlas[row * 256:(row + 1) * 256, col * 256:(col + 1) * 256] = rgba
                tiles[f"{z}/{x}/{y}"] = [len(files), col, row]
            data = encode(atlas)
            (TILES_DIR / fn).write_bytes(data)
            files.append(fn)
            total += len(data)
        print(f"  {self.name}: {len(tiles)} tiles in {len(files)} atlases, {total / 1e6:.1f} MB")
        return {"minzoom": minzoom, "maxzoom": maxzoom, "bounds": bounds_ll, "files": files, "tiles": tiles,
                "bytes": total}


def encode(rgba: np.ndarray, quality=80) -> bytes | None:
    a = rgba[..., 3]
    if not a.any():
        return None
    b = io.BytesIO()
    if a.min() == 255:
        Image.fromarray(rgba[..., :3]).save(b, "WEBP", quality=quality, method=4)
    else:
        Image.fromarray(rgba).save(b, "WEBP", quality=quality, method=4)
    return b.getvalue()


def pad_even(arr, tx0, ty0, fill):
    """Pad (H, W, C) so that tile origin and tile counts are even (ready for 2x downsampling)."""
    H, W = arr.shape[:2]
    pl = 256 if tx0 % 2 else 0
    pt = 256 if ty0 % 2 else 0
    pr = 256 if ((W + pl) // 256) % 2 else 0
    pb = 256 if ((H + pt) // 256) % 2 else 0
    arr = np.pad(arr, ((pt, pb), (pl, pr), (0, 0)), constant_values=fill)
    return arr, tx0 - pl // 256, ty0 - pt // 256


def pyramid(arr, tx0, ty0, zmax, zmin, to_rgba, down, packer, fill):
    for z in range(zmax, zmin - 1, -1):
        H, W = arr.shape[:2]
        for j in range(H // 256):
            for i in range(W // 256):
                t = to_rgba(arr[j * 256:(j + 1) * 256, i * 256:(i + 1) * 256])
                if t[..., 3].any():
                    packer.add(z, tx0 + i, ty0 + j, t)
        if z == zmin:
            break
        arr, tx0, ty0 = pad_even(arr, tx0, ty0, fill)
        arr = down(arr)
        tx0, ty0 = tx0 // 2, ty0 // 2


# ---- RGB (uint8 RGBA, premultiplied averaging)
def rgb_down(arr):
    a = arr[..., 3:4].astype(np.float32) / 255
    p = arr[..., :3].astype(np.float32) * a
    H, W = arr.shape[:2]
    p = p.reshape(H // 2, 2, W // 2, 2, 3).mean((1, 3))
    a = a.reshape(H // 2, 2, W // 2, 2, 1).mean((1, 3))
    rgb = np.where(a > 0, p / np.maximum(a, 1e-6), 0)
    return np.concatenate([rgb, a * 255], -1).round().clip(0, 255).astype(np.uint8)


def rgb_rgba(t):
    t = t.copy()
    t[..., 3] = np.where(t[..., 3] >= 128, 255, t[..., 3])
    return t


# ---- float layers (NaN = no data)
def float_down(arr):
    H, W, C = arr.shape
    with np.errstate(invalid="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            return np.nanmean(arr.reshape(H // 2, 2, W // 2, 2, C), axis=(1, 3)).astype(np.float32)


def ndvi_rgba_factory():
    import matplotlib
    cmap = matplotlib.colormaps["RdYlGn"]
    lo, hi = NDVI_RANGE

    def f(t):
        v = t[..., 0]
        ok = np.isfinite(v)
        rgba = (cmap(np.clip((np.nan_to_num(v) - lo) / (hi - lo), 0, 1)) * 255).astype(np.uint8)
        rgba[..., 3] = np.where(ok, 255, 0)
        return rgba
    return f


def veg_rgba(t):
    v = t[..., 0]
    ok = np.isfinite(v)
    frac = np.nan_to_num(v)
    rgba = np.zeros(v.shape + (4,), np.uint8)
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = 20, 230, 120
    rgba[..., 3] = np.where(ok, np.clip(frac * 255 * 1.6, 0, 230), 0).astype(np.uint8)
    return rgba


def reproject_float(src_arr, src_tr, crs, grid):
    tx0, ty0, W, H, tr = grid
    dst = np.full((H, W), np.nan, np.float32)
    reproject(src_arr, dst, src_transform=src_tr, src_crs=crs, dst_transform=tr, dst_crs="EPSG:3857",
              src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.average, num_threads=4)
    return dst


def main(input_dir=DEFAULT_INPUT, rgb_zmax=23, zmax=22, zmin=16) -> dict:
    from detect_plants import _nan_gauss, _robust_z

    rgb_path, _ = find_inputs(input_dir)
    index = {"format": "webp-atlas", "chunk_shift": CHUNK_SHIFT, "tile_size": 256, "ndvi_range": NDVI_RANGE,
             "layers": {}}

    with rasterio.open(OUT / "aligned" / "ndvi_coreg.tif") as d:
        nd = d.read(1).astype(np.float32)
        nd_tr, crs = d.transform, d.crs
        b3857 = transform_bounds(crs, "EPSG:3857", *d.bounds, densify_pts=21)
        bll = [round(v, 7) for v in transform_bounds(crs, "EPSG:4326", *d.bounds, densify_pts=21)]
    nd[nd < -2] = np.nan

    # --- NDVI
    print("NDVI tiles ...")
    g = target_grid(b3857, zmax)
    arr = reproject_float(nd, nd_tr, crs, g)[..., None]
    p = Packer("ndvi")
    pyramid(arr, g[0], g[1], zmax, zmin, ndvi_rgba_factory(), float_down, p, np.nan)
    index["layers"]["ndvi"] = p.write(zmin, zmax, bll)

    # --- vegetation mask (NDVI above local soil background)
    print("Vegetation mask tiles ...")
    anom = nd - _nan_gauss(nd, 40)
    z = _robust_z(anom)
    veg = np.where(np.isfinite(nd), (z >= 3).astype(np.float32), np.nan).astype(np.float32)
    del anom, z
    arr = reproject_float(veg, nd_tr, crs, g)[..., None]
    p = Packer("vegmask")
    pyramid(arr, g[0], g[1], zmax, zmin, veg_rgba, float_down, p, np.nan)
    index["layers"]["vegmask"] = p.write(zmin, zmax, bll)
    del arr, veg, nd

    # --- RGB from the original 6 mm mosaic
    print("RGB tiles (from original 6 mm mosaic) ...")
    g = target_grid(b3857, rgb_zmax)
    tx0, ty0, W, H, tr = g
    rgba = np.zeros((H, W, 4), np.uint8)
    with rasterio.open(rgb_path) as src:
        nb = 4 if src.count >= 4 else 3
        for b in range(1, nb + 1):
            band = np.zeros((H, W), np.uint8)
            reproject(rasterio.band(src, b), band, dst_transform=tr, dst_crs="EPSG:3857",
                      resampling=Resampling.average, num_threads=4)
            rgba[..., b - 1] = band
            print(f"  band {b} reprojected")
        if nb == 3:   # no alpha band: pixels with any colour are valid
            rgba[..., 3] = np.where(rgba[..., :3].max(-1) > 0, 255, 0)
    p = Packer("rgb")
    pyramid(rgba, tx0, ty0, rgb_zmax, zmin, rgb_rgba, rgb_down, p, 0)
    index["layers"]["rgb"] = p.write(zmin, rgb_zmax, bll)

    TILES_DIR.mkdir(parents=True, exist_ok=True)
    (TILES_DIR / "index.json").write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")
    return {k: {"tiles": len(v["tiles"]), "files": len(v["files"]), "MB": round(v["bytes"] / 1e6, 1)}
            for k, v in index["layers"].items()}


if __name__ == "__main__":
    print(main())
