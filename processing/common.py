"""Shared paths and helpers for the Farmwings processing pipeline."""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

# The machine-wide PROJ_LIB points at PostGIS's older proj.db; use rasterio's bundled copy.
try:
    import importlib.util as _ilu
    _spec = _ilu.find_spec("rasterio")
    if _spec and _spec.origin:
        _pd = Path(_spec.origin).parent / "proj_data"
        if _pd.exists():
            os.environ["PROJ_DATA"] = os.environ["PROJ_LIB"] = str(_pd)
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
# Every location / label can be overridden per run (the compute server runs one job per folder)
OUT = Path(os.environ.get("FARMWINGS_OUT", ROOT / "processing_outputs"))
WEB_DATA = Path(os.environ.get("FARMWINGS_WEB", ROOT / "public" / "data"))
DEFAULT_INPUT = Path(os.environ.get("FARMWINGS_INPUT", ROOT.parent / "Pilot"))
PROJECT_NAME = os.environ.get("FARMWINGS_PROJECT", "Pilot")
SPECIES = os.environ.get("FARMWINGS_SPECIES", "Rhanterium epapposum")   # planted species (installation record, block 4-9/197/RE)
BRAND = "FarmWings"
# The identification model separates planted stock from other vegetation by appearance; it does
# not recognise species (the species name comes from the planting record).
PLANTED_CLASS = "Planted stock"

PROCESSING_DATE = date.today().isoformat()


def find_inputs(input_dir: Path) -> tuple[Path, Path]:
    """Locate the RGB orthomosaic and the NDVI raster inside the input folder."""
    tifs = sorted(p for p in Path(input_dir).rglob("*.tif"))
    ndvi = [p for p in tifs if "ndvi" in p.name.lower()]
    rgb = [p for p in tifs if "ndvi" not in p.name.lower()]
    if len(ndvi) != 1 or len(rgb) != 1:
        raise SystemExit(f"Expected exactly one RGB and one NDVI .tif in {input_dir}, got {tifs}")
    return rgb[0], ndvi[0]


def out_dir(stage: str) -> Path:
    d = OUT / stage
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=float), encoding="utf-8")


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
