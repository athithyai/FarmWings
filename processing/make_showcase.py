"""Landing-page visuals: one field patch of the Pilot rendered at every stage of the pipeline.

  public/showcase/{rgb,ndvi,detect,identify,health}.webp   same 9 m x 6 m window, 1200 x 800 px

Reads the source RGB (read-only), the co-registered NDVI and the published web results, so run it
after the pipeline. Colours match the app's map legend.
"""
from __future__ import annotations

import json

from common import DEFAULT_INPUT, OUT, ROOT, WEB_DATA  # noqa: I001 (sets PROJ_DATA first)

import cv2
import geopandas as gpd
import matplotlib
import numpy as np
import rasterio
from PIL import Image, ImageDraw
from rasterio.enums import Resampling
from rasterio.windows import from_bounds

CENTER = (793624.7, 3188782.7)   # EPSG:32638: all five health groups, an empty spot, between-line vegetation
SIZE_M = (9.0, 6.0)
OUT_PX = (1200, 800)
SS = 2                           # supersampling for smooth outlines
NDVI_RANGE = (-0.05, 0.55)
DEST = ROOT / "public" / "showcase"

HEALTH = {"Very good condition": "#12805a", "Good condition": "#6cc79f", "Fair condition": "#9a9994",
          "Poor condition": "#ef9a8a", "Very poor condition": "#c93a3a"}
IDENT = {"Other vegetation": "#d95926", "Unclassified": "#898781"}   # planted class -> #3987e5
PLANTED, BETWEEN, GAP, LINE = "#ffd166", "#5fd4e8", "#ff6f91", "#ffffff"


def rgba(hex_, a=255):
    h = hex_.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (a,)


def read_window(path, bounds, shape, resampling):
    with rasterio.open(path) as d:
        w = from_bounds(*bounds, transform=d.transform)
        return d.read(window=w, out_shape=(d.count, *shape), resampling=resampling, boundless=True, fill_value=0)


def main():
    (cx, cy), (sw, sh) = CENTER, SIZE_M
    bounds = (cx - sw / 2, cy - sh / 2, cx + sw / 2, cy + sh / 2)
    W, H = OUT_PX[0] * SS, OUT_PX[1] * SS
    to_px = lambda x, y: ((x - bounds[0]) / sw * W, (bounds[3] - y) / sh * H)   # noqa: E731

    rgb = read_window(DEFAULT_INPUT / "data" / "4_9_197.tif", bounds, (H, W), Resampling.lanczos)[:3]
    rgb = np.ascontiguousarray(np.moveaxis(rgb, 0, -1))
    nd = read_window(OUT / "aligned" / "ndvi_coreg.tif", bounds, (H, W), Resampling.bilinear)[0].astype(np.float32)
    lo, hi = NDVI_RANGE
    ndvi = (matplotlib.colormaps["RdYlGn"](np.clip((nd - lo) / (hi - lo), 0, 1))[..., :3] * 255).astype(np.uint8)

    crs = "EPSG:32638"
    plants = gpd.read_file(WEB_DATA / "plants.json").to_crs(crs).cx[bounds[0]:bounds[2], bounds[1]:bounds[3]]
    lines = gpd.read_file(WEB_DATA / "lines.json").to_crs(crs).cx[bounds[0]:bounds[2], bounds[1]:bounds[3]]
    gaps = gpd.read_file(WEB_DATA / "gaps.json").to_crs(crs).cx[bounds[0]:bounds[2], bounds[1]:bounds[3]]
    summary = json.loads((WEB_DATA / "summary.json").read_text(encoding="utf-8"))
    planted_class = summary["stats"]["planted_class"]

    def rings(geom):
        polys = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
        return [[to_px(x, y) for x, y in p.exterior.coords] for p in polys]

    def overlay(base, draw_fn, dim=0.0):
        img = Image.fromarray(base).convert("RGBA")
        if dim:
            img = Image.blend(img, Image.new("RGBA", img.size, (246, 245, 240, 255)), dim)
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw_fn(ImageDraw.Draw(layer))
        return Image.alpha_composite(img, layer).convert("RGB")

    def outline(d, geom, color, width=3 * SS, fill=None):
        for r in rings(geom):
            if fill:
                d.polygon(r, fill=fill)
            d.line(r + [r[0]], fill=(20, 24, 20, 150), width=width + 2 * SS, joint="curve")
            d.line(r + [r[0]], fill=color, width=width, joint="curve")

    def draw_lines(d, alpha=150):
        for g in lines.geometry:
            pts = [to_px(x, y) for x, y in g.coords]
            d.line(pts, fill=rgba(LINE, alpha), width=2 * SS)

    def detect(d):
        draw_lines(d, 170)
        for _, p in plants.iterrows():
            on = p.position_type == "Planting line"
            outline(d, p.geometry, rgba(PLANTED if on else BETWEEN), 3 * SS if on else 2 * SS)
        for g in gaps.geometry:
            x, y = to_px(g.x, g.y)
            r = 24 * SS
            d.ellipse([x - r, y - r, x + r, y + r], outline=rgba(GAP), width=3 * SS)

    def identify(d):
        for _, p in plants.iterrows():
            c = "#3987e5" if p.plant_class == planted_class else IDENT.get(p.plant_class, "#898781")
            outline(d, p.geometry, rgba(c), 2 * SS, fill=rgba(c, 120))

    def health(d):
        for _, p in plants.iterrows():
            c = HEALTH.get(p.health_class)
            if c and p.position_type == "Planting line":
                outline(d, p.geometry, rgba(c), 2 * SS, fill=rgba(c, 170))
            else:
                outline(d, p.geometry, (255, 255, 255, 120), 1 * SS)

    DEST.mkdir(parents=True, exist_ok=True)
    images = {
        "rgb": Image.fromarray(rgb),
        "ndvi": Image.fromarray(ndvi),
        "detect": overlay(rgb, detect),
        "identify": overlay(rgb, identify, dim=0.15),
        "health": overlay(rgb, health, dim=0.15),
    }
    for name, im in images.items():
        im.resize(OUT_PX, Image.LANCZOS).save(DEST / f"{name}.webp", "WEBP", quality=84, method=5)
    meta = {"bounds_utm": bounds, "crs": crs, "size_m": SIZE_M, "px": OUT_PX,
            "plants": int(len(plants)), "planted": int((plants.position_type == "Planting line").sum()),
            "gaps": int(len(gaps))}
    (DEST / "index.json").write_text(json.dumps(meta), encoding="utf-8")
    print(meta)


if __name__ == "__main__":
    main()
