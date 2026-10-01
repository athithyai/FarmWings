"""Write public/third-party-licenses.txt: full licence texts of the third-party code shipped in the
web app bundle (MapLibre GL JS and the packages it inlines) plus map data, style and model credits.

    python app/frontend/collect_licenses.py      (run after npm install, before npm run build)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NM = ROOT / "node_modules"
OUT = ROOT / "public" / "third-party-licenses.txt"

# maplibre-gl and the packages its dist bundle inlines (resolved from its source maps)
PACKAGES = ["maplibre-gl", "@maplibre/maplibre-gl-style-spec", "@maplibre/geojson-vt", "@maplibre/vt-pbf",
            "@maplibre/mlt", "@mapbox/point-geometry", "@mapbox/tiny-sdf", "@mapbox/unitbezier",
            "@mapbox/vector-tile", "pbf", "earcut", "kdbush", "bidi-js", "potpack", "gl-matrix", "tinyqueue",
            "murmurhash-js", "quickselect"]

CREDITS = """\
FarmWings - third-party licences and credits
============================================

FarmWings itself: Copyright (c) 2026. All rights reserved (source-available; see the LICENSE
file of the FarmWings repository). The notices below apply to the third-party components only.

Map data and style (project map)
--------------------------------
Map data (c) OpenStreetMap contributors, available under the Open Database License (ODbL 1.0),
https://www.openstreetmap.org/copyright
Vector tiles: OpenFreeMap (https://openfreemap.org), OpenMapTiles schema (c) OpenMapTiles
(https://openmaptiles.org), BSD-3-Clause / CC BY 4.0.
Map style: Positron, (c) MapTiler.com & OpenMapTiles contributors, (c) 2015 CartoDB Inc., CC BY 4.0;
based on CartoDB Basemaps designed by Stamen and Paul Norman (CC BY 3.0).

Models
------
Built with DINOv3. Plant identification and health use features from Meta's DINOv3 ViT-L/16
SAT-493M model, licensed under the DINOv3 License (https://github.com/facebookresearch/dinov3).
Plant outlines use Meta's SAM 2.1 (Apache License 2.0).

Fonts
-----
Bricolage Grotesque, IBM Plex Sans and IBM Plex Mono, SIL Open Font License 1.1, served by Google Fonts.

"""


def licence_text(pkg: str) -> tuple[str, str]:
    d = NM / pkg
    meta = json.loads((d / "package.json").read_text(encoding="utf-8"))
    files = sorted(p for p in d.iterdir() if re.match(r"(?i)^(licen[cs]e|copying)", p.name))
    if files:
        text = "\n\n".join(p.read_text(encoding="utf-8", errors="replace").strip() for p in files)
    else:   # e.g. murmurhash-js: the MIT notice lives in the README only
        readme = next((p for p in d.iterdir() if p.name.lower().startswith("readme")), None)
        text = readme.read_text(encoding="utf-8", errors="replace").strip() if readme else "(no licence file found)"
        m = re.search(r"(?is)(#+\s*licen[cs]e.*)", text)
        text = m.group(1).strip() if m else text
    return f"{meta.get('version', '?')}, declared licence: {meta.get('license', '?')}", text


def main():
    parts = [CREDITS, "Code shipped in the web app\n---------------------------\n"]
    missing = []
    for pkg in PACKAGES:
        if not (NM / pkg / "package.json").exists():
            missing.append(pkg)
            continue
        head, text = licence_text(pkg)
        parts.append(f"\n{'=' * 78}\n{pkg} {head}\n{'=' * 78}\n{text}\n")
    OUT.write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} kB); not installed (skipped): {missing or 'none'}")


if __name__ == "__main__":
    main()
