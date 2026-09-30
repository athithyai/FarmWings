"""Farmwings pipeline - one command, independently inspectable stages.

    python processing/run_pipeline.py --input "C:\\Users\\great\\Documents\\Lab\\Hari\\Pilot"
    python processing/run_pipeline.py --from identify     # resume from a stage
    python processing/run_pipeline.py --only health       # run a single stage

Stages (each writes to processing_outputs/<stage>/ and can be run on its own):
  inspect   -> inspect/inventory.json, previews                       (inspect_data.py)
  align     -> aligned/rgb_aligned.tif, ndvi_aligned.tif, ndvi_coreg.tif (align_rasters.py)
  lines     -> lines/planting_lines.geojson                          (detect_lines.py)
  detect    -> detection/plants.geojson                              (detect_plants.py)
  identify  -> identification/plant_identification.geojson           (identify_plants.py)
  health    -> health/plant_health.geojson                           (assess_health.py)
  export    -> web/combined_plants.geojson, public/data/*.json       (export_results.py)
  tiles     -> public/data/tiles/ (packed WebP XYZ tiles)            (make_tiles.py)
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from common import DEFAULT_INPUT  # noqa: I001 (sets PROJ_DATA first)

STAGES = ["inspect", "align", "lines", "detect", "identify", "health", "export", "tiles"]


def run(stage: str, input_dir: Path):
    if stage == "inspect":
        import inspect_data
        inspect_data.main(input_dir)
    elif stage == "align":
        import align_rasters
        align_rasters.main(input_dir)
    elif stage == "lines":
        import detect_lines
        detect_lines.main()
    elif stage == "detect":
        import detect_plants
        detect_plants.main()
    elif stage == "identify":
        import identify_plants
        identify_plants.main(input_dir)
    elif stage == "health":
        import assess_health
        assess_health.main()
    elif stage == "export":
        import export_results
        export_results.main()
    elif stage == "tiles":
        import make_tiles
        print(make_tiles.main(input_dir))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="folder with the RGB and NDVI GeoTIFFs")
    ap.add_argument("--from", dest="start", choices=STAGES, default=STAGES[0])
    ap.add_argument("--only", choices=STAGES)
    a = ap.parse_args()
    stages = [a.only] if a.only else STAGES[STAGES.index(a.start):]
    for s in stages:
        t = time.time()
        print(f"\n=== {s} ===")
        run(s, a.input)
        print(f"=== {s} done in {time.time() - t:.0f} s")


if __name__ == "__main__":
    main()
