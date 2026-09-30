"""Stage 4 - Plant Health Model  ("what is the vegetation condition of this plant?").

Separate from detection and identification: it reads only the detected plant polygons and
the imagery - never the identification result - so health can be filtered by class later
without the two models influencing each other.

No validated pretrained RGB+NDVI health / stress model exists for young desert seedlings at
6 mm - 2.4 cm GSD (see docs/model-evaluation.md), so this is a transparent RELATIVE
multimodal model (Approach B):

  indicators per plant (higher = more vigorous)
    ndvi_contrast   crown median NDVI - median NDVI of the plant's own soil ring
                    (0.3-0.8 m outside the crown, other crowns excluded). Cancels soil /
                    moisture differences across the field.              weight 0.30
    p90_ndvi        NDVI of the greenest crown tissue                    weight 0.20
    median_ndvi     crown NDVI                                           weight 0.15
    vari            RGB Visible Atmospherically Resistant Index (median)  weight 0.15
    green_fraction  share of crown pixels with ExG > 0.02 (RGB)           weight 0.10
    log_green_area  log(crown area x green fraction), vigour ~ size       weight 0.10
  -> robust z (median / MAD over all detected plants) -> weighted sum -> re-standardised
     composite z -> health_score = Phi(z) in [0, 1]
  classes (robust-z bands, not field quintiles, so class sizes are data-driven):
     z >= 1.5 Very high vigour | 0.5..1.5 High | -0.5..0.5 Moderate | -1.5..-0.5 Low | < -1.5 Very low

Also flags `no_green_signal` (crown p90 NDVI less than 0.03 above its soil ring AND green
fraction < 5 %): no living green tissue detectable - possibly dead, dormant or missing.

Plant health is inferred from RGB and NDVI remote-sensing indicators and is not a
laboratory disease diagnosis.

Outputs (processing_outputs/health/): plant_health.geojson, health_summary.json, health_preview.png
"""
from __future__ import annotations

from common import OUT, PROCESSING_DATE, out_dir, write_json  # noqa: I001 (sets PROJ_DATA first)

import numpy as np
from scipy.stats import norm, spearmanr

HEALTH_MODEL = "Relative multimodal vigour model (NDVI + RGB indices, robust z vs field)"
HEALTH_VERSION = "0.1.0"
DISCLAIMER = ("Plant health is inferred from RGB and NDVI remote-sensing indicators and is not a "
              "laboratory disease diagnosis.")
RING_INNER_M, RING_OUTER_M = 0.3, 0.8

WEIGHTS = {"ndvi_contrast": 0.30, "p90_ndvi": 0.20, "median_ndvi": 0.15,
           "vari": 0.15, "green_fraction": 0.10, "log_green_area": 0.10}
NDVI_SIDE = ["ndvi_contrast", "p90_ndvi", "median_ndvi"]
RGB_SIDE = ["vari", "green_fraction", "log_green_area"]
CLASSES = [(1.5, "Very high vigour"), (0.5, "High vigour"), (-0.5, "Moderate vigour"),
           (-1.5, "Low vigour"), (-np.inf, "Very low vigour")]


def robust_z(x: np.ndarray) -> np.ndarray:
    med = np.nanmedian(x)
    mad = np.nanmedian(np.abs(x - med)) * 1.4826
    return (x - med) / (mad if mad > 0 else np.nanstd(x) + 1e-9)


def classify(z: float) -> str:
    for thr, name in CLASSES:
        if z >= thr:
            return name
    return CLASSES[-1][1]


def main() -> dict:
    import geopandas as gpd
    import pandas as pd
    from features import ndvi_stats, plant_pixels, rgb_indices

    out = out_dir("health")
    gdf = gpd.read_file(OUT / "detection" / "plants.geojson")

    print("zonal statistics: crowns ...")
    nds, rgbs = plant_pixels(gdf)
    print("zonal statistics: soil rings ...")
    rings = [g.buffer(RING_OUTER_M).difference(g.buffer(RING_INNER_M)) for g in gdf.geometry]
    ring_nd, _ = plant_pixels(gdf, geoms=rings, exclude_geoms=gdf.geometry)

    rows = []
    for v, px, rv, area in zip(nds, rgbs, ring_nd, gdf.area_m2.values):
        s = ndvi_stats(v) | rgb_indices(px)
        bg = float(np.median(rv)) if rv.size >= 20 else np.nan
        s["bg_ndvi"] = bg
        s["ndvi_contrast"] = (s["median_ndvi"] - bg) if s["median_ndvi"] is not None else np.nan
        s["p90_contrast"] = (s["p90_ndvi"] - bg) if s["p90_ndvi"] is not None else np.nan
        gf = s["green_fraction"] if s["green_fraction"] is not None else 0.0
        s["green_area_m2"] = float(area * gf)
        s["log_green_area"] = float(np.log(area * gf + 1e-3))
        rows.append(s)
    f = pd.DataFrame(rows)
    # plants with no ring pixels (field edge) fall back to the field median background
    f["bg_ndvi"] = f.bg_ndvi.fillna(f.bg_ndvi.median())
    f["ndvi_contrast"] = f.ndvi_contrast.fillna(f.median_ndvi - f.bg_ndvi)
    f["p90_contrast"] = f.p90_contrast.fillna(f.p90_ndvi - f.bg_ndvi)

    Z = pd.DataFrame({k: robust_z(f[k].astype(float).values) for k in WEIGHTS}).clip(-6, 6)
    Z = Z.fillna(0.0)
    comp = sum(Z[k] * w for k, w in WEIGHTS.items())
    z = robust_z(comp.values)
    ndvi_sub = robust_z(sum(Z[k] for k in NDVI_SIDE).values)
    rgb_sub = robust_z(sum(Z[k] for k in RGB_SIDE).values)

    res = gdf[["plant_id", "geometry"]].copy()
    res["health_class"] = [classify(v) for v in z]
    res["health_score"] = norm.cdf(z).round(3)
    res["health_z"] = z.round(3)
    for k in ["mean_ndvi", "median_ndvi", "min_ndvi", "max_ndvi", "std_ndvi", "p10_ndvi", "p90_ndvi",
              "bg_ndvi", "ndvi_contrast"]:
        res[k] = f[k].astype(float).round(4)
    res["valid_pixel_count"] = f.valid_pixel_count.astype(int)
    for k in ["exg", "vari", "gli", "rg_ratio", "green_fraction", "brightness"]:
        res[k] = f[k].astype(float).round(4)
    res["green_area_m2"] = f.green_area_m2.round(4)
    res["ndvi_subscore_z"] = ndvi_sub.round(3)
    res["rgb_subscore_z"] = rgb_sub.round(3)
    res["no_green_signal"] = ((f.p90_contrast < 0.03) & (f.green_fraction.fillna(0) < 0.05)).values
    res["health_model"] = HEALTH_MODEL
    res["health_model_version"] = HEALTH_VERSION
    res["health_inputs"] = "crown NDVI stats + NDVI contrast vs soil ring + RGB VARI / ExG green fraction + green area"
    res["health_note"] = DISCLAIMER
    res["experimental"] = True
    res["processing_date"] = PROCESSING_DATE
    res.to_file(out / "plant_health.geojson", driver="GeoJSON")

    rho = spearmanr(ndvi_sub, rgb_sub, nan_policy="omit").statistic
    corr = Z.corr(method="spearman").round(3)
    summary = {
        "model": HEALTH_MODEL, "version": HEALTH_VERSION, "approach": "B - relative multimodal (no suitable pretrained model)",
        "weights": WEIGHTS, "class_bands_z": {name: thr for thr, name in CLASSES},
        "n_plants": int(len(res)),
        "class_counts": {str(k): int(v) for k, v in res.health_class.value_counts().items()},
        "no_green_signal": int(res.no_green_signal.sum()),
        "mean_health_score": float(res.health_score.mean()),
        "field_median": {k: float(np.nanmedian(f[k].astype(float))) for k in list(WEIGHTS) + ["bg_ndvi", "mean_ndvi"]},
        "consistency": {
            "spearman_ndvi_subscore_vs_rgb_subscore": float(rho),
            "indicator_spearman_matrix": corr.to_dict(),
        },
        "disclaimer": DISCLAIMER, "processing_date": PROCESSING_DATE,
    }
    write_json(out / "health_summary.json", summary)
    preview(res, out / "health_preview.png")
    print({k: v for k, v in summary.items() if k not in ("consistency", "weights", "class_bands_z", "field_median")})
    print("NDVI vs RGB sub-score Spearman:", round(float(rho), 3))
    return summary


def preview(res, path, window=(4245, 2448, 1024)):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import rasterio
    from rasterio.windows import Window
    colors = {"Very high vigour": "#1a9641", "High vigour": "#a6d96a", "Moderate vigour": "#ffffbf",
              "Low vigour": "#fdae61", "Very low vigour": "#d7191c"}
    c0, r0, s = window
    with rasterio.open(OUT / "aligned" / "rgb_aligned.tif") as d:
        img = np.moveaxis(d.read([1, 2, 3], window=Window(c0, r0, s, s)), 0, -1)
        x0, y0 = d.transform * (c0, r0)
        x1, y1 = d.transform * (c0 + s, r0 + s)
    fig, ax = plt.subplots(figsize=(10, 10))
    ax.imshow(img, extent=[x0, x1, y1, y0])
    sub = res.cx[x0:x1, y1:y0]
    for k, col in colors.items():
        part = sub[sub.health_class == k]
        if len(part):
            part.plot(ax=ax, color=col, alpha=0.55, edgecolor="k", linewidth=0.4)
    ax.set_xlim(x0, x1); ax.set_ylim(y1, y0); ax.axis("off")
    ax.set_title("Plant Health (relative vigour) - green = high, red = low")
    plt.tight_layout(); plt.savefig(path, dpi=90); plt.close()


if __name__ == "__main__":
    main()
