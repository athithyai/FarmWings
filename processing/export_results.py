"""Stage 5 - join the three independent stage outputs into one plant dataset + web data.

Inputs : detection/plants.geojson, identification/plant_identification.geojson,
         health/plant_health.geojson, lines/planting_lines.geojson, stage summaries
Outputs: <out>/web/combined_plants.geojson  (full, source CRS, all provenance fields)
         <web>/plants.json      (EPSG:4326, compact, drives the app)
         <web>/lines.json       (visible planting-line stretches, EPSG:4326)
         <web>/summary.json     (lifecycle, statistics, model provenance, evaluation)
"""
from __future__ import annotations

import json

from common import (BRAND, OUT, PROCESSING_DATE, PROJECT_NAME, SPECIES, WEB_DATA,  # noqa: I001
                    out_dir, read_json, write_json)

import numpy as np

WEB_FIELDS = [
    # detection
    "plant_id", "line_id", "position_type", "dist_to_line_m", "drip_line_visibility", "area_m2",
    "equiv_diameter_m", "detection_confidence", "sam_iou", "core_found", "detection_source", "outline",
    "n_parts", "filled_gap",
    # identification
    "plant_class", "identification_confidence", "p_planted",
    # health
    "health_class", "health_group", "health_confidence", "health_score",
    "vigour_class", "vigour_score", "mean_ndvi", "median_ndvi", "min_ndvi", "max_ndvi",
    "std_ndvi", "p10_ndvi", "p90_ndvi", "valid_pixel_count", "bg_ndvi", "ndvi_contrast",
    "vari", "exg", "green_fraction", "rg_ratio", "brightness", "green_area_m2",
    "canopy_area_m2", "living_canopy", "canopy_status",
    "lon", "lat",
]


def _opt(path):
    return read_json(path) if path.exists() else None


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

    WEB_DATA.mkdir(parents=True, exist_ok=True)
    fields = [f for f in WEB_FIELDS if f in comb.columns]
    web = comb[fields + ["geometry"]].copy()
    web["geometry"] = web.geometry.simplify(0.01)
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

    gaps_path = OUT / "detection" / "planting_gaps.geojson"
    gfeats = []
    if gaps_path.exists():
        gaps = gpd.read_file(gaps_path)
        if len(gaps):
            gaps = gaps.to_crs(4326)
            gfeats = [{"type": "Feature", "properties": {"line_id": r.line_id, "status": r.status, "gap_m": float(r.gap_m)},
                       "geometry": {"type": "Point", "coordinates": [round(r.geometry.x, 7), round(r.geometry.y, 7)]}}
                      for r in gaps.itertuples()]
    (WEB_DATA / "gaps.json").write_text(json.dumps({"type": "FeatureCollection", "features": gfeats},
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
    trial_d = _opt(OUT / "experiments" / "detection_trial_v1_baselines.json")
    trial_i = _opt(OUT / "experiments" / "identification_trial.json")

    planted_cls = f"{SPECIES} (planted)"
    planted = comb[comb.on_planting_line]
    classes = hea["classes"]
    hc = planted.health_class.value_counts()
    good = int(sum(hc.get(c, 0) for c in classes if "good" in c.lower()))
    poor = int(sum(hc.get(c, 0) for c in classes if "poor" in c.lower()))
    area_ha = ali["valid_area_m2"] / 1e4
    canopy = planted["living_canopy"].fillna(False).astype(bool) if "living_canopy" in planted else None
    stats = {
        "planted_positions": int(len(planted)),
        "expected_planting_positions": int(det.get("expected_planting_positions", len(planted))),
        "inferred_missing_positions": int(det.get("inferred_missing_positions", 0)),
        "green_canopy_planted": int(canopy.sum()) if canopy is not None else None,
        "no_green_canopy_planted": int((~canopy).sum()) if canopy is not None else None,
        "median_canopy_m2_planted": float(planted.canopy_area_m2.median()) if "canopy_area_m2" in planted else None,
        "planted_identified_as_species": int((planted.plant_class == planted_cls).sum()),
        "between_line_vegetation": int((~comb.on_planting_line).sum()),
        "all_detections": int(len(comb)),
        "identified": int((comb.plant_class != "Unclassified").sum()),
        "identification_rate": float((comb.plant_class != "Unclassified").mean()),
        "class_counts": {str(k): int(v) for k, v in comb.plant_class.value_counts().items()},
        "planted_class": planted_cls,
        "planted_identified": int((comb.plant_class == planted_cls).sum()),
        "health_counts_planted": {c: int(hc.get(c, 0)) for c in classes},
        "good_condition_planted": good, "poor_condition_planted": poor,
        "good_share_planted": good / max(len(planted), 1),
        "avg_health_score_planted": float(planted.health_score.mean()),
        "avg_mean_ndvi_planted": float(planted.mean_ndvi.mean()),
        "field_soil_ndvi_median": float(hea["field_median"]["bg_ndvi"]),
        "planting_lines": lin["n_lines"], "line_spacing_m": lin["median_line_spacing_m"],
        "planted_density_per_ha": len(planted) / area_ha,
        "surveyed_area_ha": area_ha,
        "ndvi_recall_detections": int((comb.get("detection_source") == "NDVI vegetation patch").sum())
        if "detection_source" in comb else 0,
    }
    rgb, ndvi = inv["rgb"], inv["ndvi"]
    rejected = None
    if trial_d:
        rejected = {"DeepForest tree model (weecology/deepforest-tree)": "0 detections on 3 test tiles at 2.4 cm and 10 cm (score>0.1)",
                    "SAM 2.1 automatic mask generation": f"{trial_d['planting_grid']['sam_amg_masks']} unselective masks per 24 m tile (soil, lines)"}
    return {
        "project": PROJECT_NAME, "brand": BRAND, "subtitle": "Drone Mapping & Plant Intelligence",
        "processing_date": PROCESSING_DATE,
        "declared_species": SPECIES,
        "lifecycle": [
            {"step": "Data received", "status": "done", "detail": f"RGB {rgb['pixel_size_m'][0] * 1000:.0f} mm + NDVI {ndvi['pixel_size_m'][0] * 100:.2f} cm, {rgb['crs']}"},
            {"step": "Aligned & co-registered", "status": "done", "detail": f"median residual shift {ali['coregistration']['shift_m_median_magnitude'] * 100:.1f} cm corrected"},
            {"step": "Planting lines", "status": "done" if lin["n_lines"] else "partial", "detail": f"{lin['n_lines']} drip lines, {(lin['median_line_spacing_m'] or 0):.2f} m spacing"},
            {"step": "Plant detection", "status": "done", "detail": f"{stats['planted_positions']:,} planted positions + {stats['between_line_vegetation']:,} between-line vegetation"},
            {"step": "Identification", "status": "partial", "detail": f"{stats['identification_rate'] * 100:.0f}% identified; experimental, weakly supervised"},
            {"step": "Health analysis", "status": "done", "detail": f"{hea['k']} unsupervised condition groups for planted saplings; experimental"},
            {"step": "Results published", "status": "done", "detail": "FarmWings app"},
        ],
        "stats": stats,
        "models": {
            "detection": {"name": det["model"], "version": det["version"],
                          "summary": "RGB darkness + NDVI anomaly candidates, drip-line context, SAM 2.1 hiera-large point+box prompts on 1.18 cm RGB, core trimming, NDVI recall pass",
                          "confidence_note": "heuristic score (SAM mask IoU x candidate strength), not a calibrated probability",
                          "by_source": det.get("by_source", {}),
                          "rejected": rejected},
            "identification": {"name": ide["model"], "backbone": ide["backbone"], "version": ide["version"],
                               "classes": ide["classes"], "min_confidence": ide["min_confidence"],
                               "trained_on": ide.get("trained_on"),
                               "spatial_cv": ide.get("spatial_cv"), "weak_labels": ide["weak_labels"],
                               "experimental": True, "trial": trial_i},
            "health": {"name": hea["model"], "version": hea["version"], "approach": hea["approach"],
                       "classes": classes, "groups": hea["groups"], "k": hea["k"], "bic_by_k": hea["bic_by_k"],
                       "population": hea.get("population"), "canopy_rule": hea.get("canopy_rule"),
                       "stability_ari_mean": hea["stability_ari_mean"], "mean_confidence": hea["mean_confidence"],
                       "spearman_score_vs_vigour_index": hea["spearman_score_vs_vigour_index"],
                       "vigour_index": hea["vigour_index"],
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
