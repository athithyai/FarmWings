"""FarmWings pipeline - one command, independently inspectable stages.

    python processing/run_pipeline.py --input path/to/Pilot
    python processing/run_pipeline.py --from identify          # resume from a stage
    python processing/run_pipeline.py --only health            # run a single stage
    python processing/run_pipeline.py --input job/input --out job/outputs --web job/web \\
        --project "North block" --species Palm                  # what the compute server runs

Stages (each writes to <out>/<stage>/ and can be run on its own):
  inspect   -> inspect/inventory.json, previews                          (inspect_data.py)
  align     -> aligned/rgb_aligned.tif, rgb_fine.tif, ndvi_coreg.tif     (align_rasters.py)
  lines     -> lines/planting_lines.geojson                              (detect_lines.py)
  detect    -> detection/plants.geojson                                  (detect_plants.py)
  identify  -> identification/plant_identification.geojson               (identify_plants.py)
  health    -> health/plant_health.geojson                               (assess_health.py)
  export    -> web/combined_plants.geojson, <web>/*.json                 (export_results.py)
  tiles     -> <web>/tiles/ (WebP atlases)                               (make_tiles.py)
  media     -> <web>/media/ (plant thumbnails), <web>/figures/           (export_media.py)

Progress lines "=== <stage> ===" and "=== <stage> done in N s" are parsed by the compute server.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

STAGES = ["inspect", "align", "lines", "detect", "identify", "health", "export", "tiles", "media"]


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
        assess_health.main(input_dir)
    elif stage == "export":
        import export_results
        export_results.main()
    elif stage == "tiles":
        import make_tiles
        print(make_tiles.main(input_dir))
    elif stage == "media":
        import export_media
        export_media.main()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", type=Path, help="folder with the RGB and NDVI GeoTIFFs (default: ../Pilot)")
    ap.add_argument("--out", type=Path, help="stage outputs folder (default: processing_outputs)")
    ap.add_argument("--web", type=Path, help="web data folder (default: public/data)")
    ap.add_argument("--project", help='project name shown in the app (default "Pilot")')
    ap.add_argument("--species", help='declared planted species (default "Palm")')
    ap.add_argument("--from", dest="start", choices=STAGES, default=STAGES[0])
    ap.add_argument("--only", choices=STAGES)
    ap.add_argument("--skip", default="", help="comma-separated optional stages to skip: identify,health")
    a = ap.parse_args()
    # settings must be in the environment before common.py is imported by any stage
    for flag, env in [(a.input, "FARMWINGS_INPUT"), (a.out, "FARMWINGS_OUT"), (a.web, "FARMWINGS_WEB"),
                      (a.project, "FARMWINGS_PROJECT"), (a.species, "FARMWINGS_SPECIES")]:
        if flag is not None:
            os.environ[env] = str(Path(flag).resolve()) if isinstance(flag, Path) else flag
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import DEFAULT_INPUT  # noqa: E402

    stages = [a.only] if a.only else STAGES[STAGES.index(a.start):]
    skip = {x.strip() for x in a.skip.split(",") if x.strip()} & {"identify", "health"}
    for x in skip:   # a skipped model must not leave an older run's results behind
        os.environ[f"FARMWINGS_SKIP_{x.upper()}"] = "1"
    stages = [x for x in stages if x not in skip]
    for s in stages:
        t = time.time()
        print(f"\n=== {s} ===", flush=True)
        run(s, DEFAULT_INPUT)
        print(f"=== {s} done in {time.time() - t:.0f} s", flush=True)


if __name__ == "__main__":
    main()
