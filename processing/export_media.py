"""Stage 8 - media for the app: per-plant thumbnails and figures.

  <web>/media/rgb_XX.webp, ndvi_XX.webp   16 x 16 atlases of 128 px thumbnails, one per plant, in
                                         plants.json order (1.2 m window: RGB from the 6 mm
                                         mosaic, NDVI colour-mapped like the NDVI map layer)
  <web>/media/index.json                 atlas layout + plant order
  <web>/figures/*.webp                   stage previews used by the Overview / Methodology screens
"""
from __future__ import annotations

import io
import json

from common import OUT, WEB_DATA, read_json  # noqa: I001 (sets PROJ_DATA first)

import cv2
import numpy as np
import rasterio
from PIL import Image
from rasterio.windows import Window

THUMB = 128
COLS = 16
WINDOW_M = 1.2
NDVI_RANGE = (-0.05, 0.55)

FIGURES = {   # name in the app -> source image (optional ones are skipped when absent)
    "field_rgb": "inspect/rgb_preview.png",
    "field_ndvi": "inspect/ndvi_preview.png",
    "detection": "detection/detection_preview.png",
    "identification": "identification/identification_examples.png",
    "health": "health/health_preview.png",
    "health_groups": "health/health_groups.png",
    "trial_detection": "experiments/detection_trial_planting_grid.png",
    "trial_lines": "experiments/vis_bands.png",
    "trial_crown_crops": "experiments/crown_crops_check.png",
}


def webp(img: np.ndarray, quality=82) -> bytes:
    b = io.BytesIO()
    Image.fromarray(img).save(b, "WEBP", quality=quality, method=4)
    return b.getvalue()


def ndvi_thumbs(gdf) -> np.ndarray:
    import matplotlib
    cmap = matplotlib.colormaps["RdYlGn"]
    lo, hi = NDVI_RANGE
    with rasterio.open(OUT / "aligned" / "ndvi_coreg.tif") as d:
        half = int(round(WINDOW_M / abs(d.transform.a) / 2))
        rows, cols = rasterio.transform.rowcol(d.transform, gdf.centroid_x.values, gdf.centroid_y.values)
        out = np.zeros((len(gdf), THUMB, THUMB, 3), np.uint8)
        for k, (r, c) in enumerate(zip(rows, cols)):
            a = d.read(1, window=Window(c - half, r - half, 2 * half, 2 * half), boundless=True, fill_value=-32767)
            ok = a > -2
            rgba = (cmap(np.clip((np.where(ok, a, lo) - lo) / (hi - lo), 0, 1)) * 255).astype(np.uint8)[..., :3]
            rgba[~ok] = 20
            out[k] = cv2.resize(rgba, (THUMB, THUMB), interpolation=cv2.INTER_NEAREST)
    return out


def atlases(thumbs: np.ndarray, prefix: str, dest) -> list[str]:
    per = COLS * COLS
    files = []
    for i in range(0, len(thumbs), per):
        block = thumbs[i:i + per]
        atlas = np.zeros((COLS * THUMB, COLS * THUMB, 3), np.uint8)
        for j, t in enumerate(block):
            r, c = divmod(j, COLS)
            atlas[r * THUMB:(r + 1) * THUMB, c * THUMB:(c + 1) * THUMB] = t
        used_rows = (len(block) + COLS - 1) // COLS
        name = f"{prefix}_{i // per:02d}.webp"
        (dest / name).write_bytes(webp(atlas[:used_rows * THUMB]))
        files.append(name)
    return files


def main() -> dict:
    import geopandas as gpd
    web = read_json(WEB_DATA / "plants.json")
    ids = [f["properties"]["plant_id"] for f in web["features"]]
    gdf = gpd.read_file(OUT / "detection" / "plants.geojson").set_index("plant_id").loc[ids].reset_index()

    media = WEB_DATA / "media"
    media.mkdir(parents=True, exist_ok=True)
    for old in media.glob("*.webp"):
        old.unlink()
    if not (OUT / "cache" / "crops_1p2m_224.npy").exists():   # identification was skipped
        from common import DEFAULT_INPUT
        from identify_plants import load_crops
        load_crops(gpd.read_file(OUT / "detection" / "plants.geojson"), DEFAULT_INPUT)
    crops = np.load(OUT / "cache" / "crops_1p2m_224.npy", mmap_mode="r")
    cache_ids = list(np.load(OUT / "cache" / "crops_ids.npy", allow_pickle=True))
    pos = {p: i for i, p in enumerate(cache_ids)}
    rgb = np.stack([cv2.resize(np.asarray(crops[pos[p]]), (THUMB, THUMB), interpolation=cv2.INTER_AREA) for p in ids])
    nd = ndvi_thumbs(gdf)
    index = {"thumb": THUMB, "cols": COLS, "window_m": WINDOW_M, "ids": ids,
             "rgb": atlases(rgb, "rgb", media), "ndvi": atlases(nd, "ndvi", media)}
    (media / "index.json").write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")

    figs = WEB_DATA / "figures"
    figs.mkdir(parents=True, exist_ok=True)
    written = {}
    for name, src in FIGURES.items():
        p = OUT / src
        if not p.exists():
            continue
        im = Image.open(p)
        im = im.convert("RGBA") if name.startswith("field_") else im.convert("RGB")
        if max(im.size) > 1800:
            im.thumbnail((1800, 1800))
        b = io.BytesIO()
        im.save(b, "WEBP", quality=84, method=4)
        (figs / f"{name}.webp").write_bytes(b.getvalue())
        written[name] = [im.size[0], im.size[1]]
    (figs / "index.json").write_text(json.dumps(written), encoding="utf-8")
    print(f"media: {len(ids)} thumbnails in {len(index['rgb'])}+{len(index['ndvi'])} atlases; figures {list(written)}")
    return {"thumbnails": len(ids), "figures": list(written)}


if __name__ == "__main__":
    main()
