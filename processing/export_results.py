"""Stage 5 - join the three independent stage outputs into one plant dataset + web data.

Inputs : detection/plants.geojson, identification/plant_identification.geojson,
         health/plant_health.geojson, lines/planting_lines.geojson, stage summaries
Outputs: processing_outputs/web/combined_plants.geojson  (full, EPSG:32638, all provenance fields)
         public/data/plants.json      (EPSG:4326, compact, drives the viewer)
         public/data/lines.json       (planting lines, EPSG:4326)
         public/data/summary.json     (lifecycle, statistics, model provenance, evaluation)
"""
from __future__ import annotations

import json

from common import OUT, PROCESSING_DATE, WEB_DATA, out_dir, read_json, write_json  # noqa: I001

import numpy as np

WEB_FIELDS = [
    # detection
    "plant_id", "line_id", "position_type", "dist_to_line_m", "area_m2", "equiv_diameter_m",
    "detection_confidence", "sam_iou", "core_found",
    # identification
    "plant_class", "identification_confidence", "p_planted_palm",
    # health
    "health_class", "health_score", "health_z", "mean_ndvi", "median_ndvi", "min_ndvi", "max_ndvi",
    "std_ndvi", "p10_ndvi", "p90_ndvi", "valid_pixel_count", "bg_ndvi", "ndvi_contrast",
    "vari", "exg", "green_fraction", "rg_ratio", "brightness", "green_area_m2", "no_green_signal",
    "lon", "lat",
]


def main() -> dict:
    import geopandas as gpd

    det = gpd.read_file(OUT / "detection" / "plants.geojson")
    ide = gpd.read_file(OUT / "identification" / "plant_identification.geojson").drop(columns="geometry")
    hea = gpd.read_file(OUT / "health" / "plant_health.geojson").drop(columns="geometry")
    ide = ide.rename(columns={"predicted_class": "plant_class", "prediction_confidence": "identification_confidence",
                              "model_version": "identification_model_version",
                              "processing_date": "identification_date", "experimental": "identification_experimental"})
    hea = hea.rename(columns={"processing_date": "health_date", "experimental": "health_experimental"})
    det = det.rename(columns={"processing_date": "detection_date"})
    comb = det.merge(ide, on="plant_id", how="left", validate="1:1").merge(hea, on="plant_id", how="left", validate="1:1")
    assert len(comb) == len(det)

    wdir = out_dir("web")
    comb.to_file(wdir / "combined_plants.geojson", driver="GeoJSON")

    # ---- compact web copy
    WEB_DATA.mkdir(parents=True, exist_ok=True)
    web = comb[WEB_FIELDS + ["geometry"]].copy()
    web["geometry"] = web.geometry.simplify(0.02)  # ~1 NDVI pixel
    web = web.to_crs(4326)
    feats = []
    for rec, geom in zip(web.drop(columns="geometry").to_dict("records"), web.geometry):
        props = {}
        for k, v in rec.items():
            if isinstance(v, (float, np.floating)):
                v = None if not np.isfinite(v) else round(float(v), 7 if k in ("lon", "lat") else 3)
            elif isinstance(v, (np.integer,)):
                v = int(v)
            elif isinstance(v, (np.bool_,)):
                v = bool(v)
            props[k] = v
        ring = [[round(x, 7), round(y, 7)] for x, y in geom.exterior.coords]
        feats.append({"type": "Feature", "properties": props, "geometry": {"type": "Polygon", "coordinates": [ring]}})
    (WEB_DATA / "plants.json").write_text(json.dumps({"type": "FeatureCollection", "features": feats},
                                                    separators=(",", ":")), encoding="utf-8")

    lines = gpd.read_file(OUT / "lines" / "planting_lines.geojson").to_crs(4326)
    lfeats = [{"type": "Feature", "properties": {"line_id": r.line_id, "length_m": float(r.length_m)},
               "geometry": {"type": "LineString", "coordinates": [[round(x, 7), round(y, 7)] for x, y in r.geometry.coords]}}
              for r in lines.itertuples()]
    (WEB_DATA / "lines.json").write_text(json.dumps({"type": "FeatureCollection", "features": lfeats},
                                                   separators=(",", ":")), encoding="utf-8")

    summary = build_summary(comb)
    write_json(WEB_DATA / "summary.json", summary)
    print(json.dumps(summary["stats"], indent=1))
    return summary


def build_summary(comb) -> dict:
    inv = read_json(OUT / "inspect" / "inventory.json")
    ali = read_json(OUT / "aligned" / "alignment.json")
    lin = read_json(OUT / "lines" / "lines_summary.json")
    det = read_json(OUT / "detection" / "detection_summary.json")
    ide = read_json(OUT / "identification" / "identification_summary.json")
    hea = read_json(OUT / "health" / "health_summary.json")
    trial_d = read_json(OUT / "experiments" / "detection_trial_v1_baselines.json")
    trial_i = read_json(OUT / "experiments" / "identification_trial.json")

    planted = comb[comb.on_planting_line]
    palms = comb[comb.plant_class == "Palm (planted)"]
    hc = planted.health_class.value_counts()
    high = int(hc.get("Very high vigour", 0) + hc.get("High vigour", 0))
    low = int(hc.get("Low vigour", 0) + hc.get("Very low vigour", 0))
    area_ha = ali["valid_area_m2"] / 1e4
    stats = {
        "planted_positions": int(len(planted)),
        "between_line_vegetation": int((~comb.on_planting_line).sum()),
        "all_detections": int(len(comb)),
        "identified": int((comb.plant_class != "Unclassified").sum()),
        "identification_rate": float((comb.plant_class != "Unclassified").mean()),
        "class_counts": {str(k): int(v) for k, v in comb.plant_class.value_counts().items()},
        "palms_identified": int(len(palms)),
        "health_counts_planted": {str(k): int(v) for k, v in hc.items()},
        "high_vigour_planted": high, "low_vigour_planted": low,
        "high_vigour_share_planted": high / max(len(planted), 1),
        "avg_health_score_planted": float(planted.health_score.mean()),
        "avg_mean_ndvi_planted": float(planted.mean_ndvi.mean()),
        "field_soil_ndvi_median": float(hea["field_median"]["bg_ndvi"]),
        "planting_lines": lin["n_lines"], "line_spacing_m": lin["median_line_spacing_m"],
        "planted_density_per_ha": len(planted) / area_ha,
        "surveyed_area_ha": area_ha,
    }
    rgb, ndvi = inv["rgb"], inv["ndvi"]
    return {
        "project": "Hari Pilot", "brand": "Farmwings", "subtitle": "Drone Mapping & Plant Intelligence",
        "processing_date": PROCESSING_DATE,
        "declared_species": "Palm (as supplied by the project; not confirmable from imagery at seedling size)",
        "lifecycle": [
            {"step": "Data received", "status": "done", "detail": f"RGB {rgb['pixel_size_m'][0]*1000:.0f} mm + NDVI {ndvi['pixel_size_m'][0]*100:.2f} cm, {rgb['crs']}"},
            {"step": "Aligned & co-registered", "status": "done", "detail": f"median residual shift {ali['coregistration']['shift_m_median_magnitude']*100:.1f} cm corrected"},
            {"step": "Planting lines", "status": "done", "detail": f"{lin['n_lines']} drip lines, {lin['median_line_spacing_m']:.2f} m spacing"},
            {"step": "Plant detection", "status": "done", "detail": f"{stats['planted_positions']:,} planted positions + {stats['between_line_vegetation']:,} between-line vegetation"},
            {"step": "Identification", "status": "partial", "detail": f"{stats['identification_rate']*100:.0f}% identified; experimental, weakly supervised"},
            {"step": "Health analysis", "status": "done", "detail": "relative vigour index (RGB + NDVI); experimental"},
            {"step": "Results published", "status": "done", "detail": "static viewer"},
        ],
        "stats": stats,
        "models": {
            "detection": {"name": det["model"], "version": det["version"],
                          "summary": "RGB darkness + NDVI anomaly candidates, drip-line context, SAM 2.1 hiera-large point+box prompts, core trimming",
                          "confidence_note": "heuristic score (SAM mask IoU x candidate strength), not a calibrated probability",
                          "rejected": {"DeepForest tree model (weecology/deepforest-tree)": f"0 detections on 3 test tiles at 2.4 cm and 10 cm (score>0.1)",
                                       "SAM 2.1 automatic mask generation": f"{trial_d['planting_grid']['sam_amg_masks']} unselective masks per 24 m tile (soil, lines)"}},
            "identification": {"name": ide["model"], "backbone": ide["backbone"], "version": ide["version"],
                               "classes": ide["classes"], "min_confidence": ide["min_confidence"],
                               "spatial_cv": ide["spatial_cv"], "weak_labels": ide["weak_labels"],
                               "experimental": True,
                               "trial": trial_i},
            "health": {"name": hea["model"], "version": hea["version"], "approach": hea["approach"],
                       "weights": hea["weights"], "class_bands_z": hea["class_bands_z"],
                       "consistency_spearman_ndvi_vs_rgb": hea["consistency"]["spearman_ndvi_subscore_vs_rgb_subscore"],
                       "experimental": True, "disclaimer": hea["disclaimer"]},
        },
        "data": {
            "rgb": {"file": rgb["file"], "pixel_size_m": rgb["pixel_size_m"][0], "size": [rgb["width"], rgb["height"]]},
            "ndvi": {"file": ndvi["file"], "pixel_size_m": ndvi["pixel_size_m"][0], "size": [ndvi["width"], ndvi["height"]],
                     "range": [ndvi["value_stats"]["min"], ndvi["value_stats"]["max"]], "median": ndvi["value_stats"]["median"]},
            "crs": rgb["crs"],
        },
    }


if __name__ == "__main__":
    main()
