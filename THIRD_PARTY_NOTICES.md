# Third-party notices

FarmWings itself is source-available, all rights reserved ([LICENSE](LICENSE)). The components below
keep their own licences and terms. Checked against primary sources on 2026-10-01; licences change,
so re-check before a commercial release.

## Shipped with the web app

The static site bundles **MapLibre GL JS 6.11.2** (BSD-3-Clause) and the packages it inlines:
@maplibre/maplibre-gl-style-spec, @maplibre/geojson-vt, @maplibre/vt-pbf, @maplibre/mlt,
@mapbox/point-geometry, @mapbox/tiny-sdf, @mapbox/unitbezier, @mapbox/vector-tile, pbf, earcut, kdbush,
bidi-js, potpack, gl-matrix, tinyqueue, murmurhash-js, quickselect (BSD-2/3-Clause, ISC, MIT,
MIT OR Apache-2.0). Their full licence texts ship with the site as
[`public/third-party-licenses.txt`](public/third-party-licenses.txt), linked as "Credits" from the
maps and the footer. Regenerate it with `python app/frontend/collect_licenses.py` after updating packages.
Vite 8 (MIT) is a build tool only and is not shipped.

## Services used by the web app

| Service | Terms | What FarmWings does |
|---|---|---|
| OpenFreeMap tiles, OpenMapTiles schema, OpenStreetMap data | OpenFreeMap terms; BSD-3-Clause / CC BY 4.0; ODbL 1.0 | On-map attribution "OpenFreeMap © OpenMapTiles Data from OpenStreetMap" (shown automatically) |
| Positron map style | CC BY 4.0 (© MapTiler & OpenMapTiles contributors, © 2015 CartoDB Inc.; after CartoDB Basemaps by Stamen and Paul Norman, CC BY 3.0) | Credit in the "Credits" page linked from the map |
| Google Fonts: Bricolage Grotesque, IBM Plex Sans, IBM Plex Mono | Fonts: SIL OFL 1.1; API: Google APIs Terms of Service | Loaded from fonts.googleapis.com, unmodified |
| Sign in with Google (Google Identity Services) | Google APIs Terms of Service, Google sign-in branding guidelines | Standard Google button; enabled once a client ID is set |
| GitHub Pages | GitHub Terms of Service | Demo hosting. GitHub Pages may not be used to run a commercial SaaS or e-commerce site: move hosting before selling the app as a service. GitHub logs visitor IP addresses. |

## Models

| Model | Licence | Use in FarmWings |
|---|---|---|
| SAM 2.1 hiera-large / tiny (facebook/sam2.1-hiera-*), code facebookresearch/sam2 | Apache-2.0 (code also BSD-3-Clause for the CUDA connected-components kernel) | Plant outlines; weights downloaded at run time |
| **DINOv3 ViT-L/16 SAT-493M** (facebook/dinov3-vitl16-pretrain-sat493m) | **DINOv3 License** ([copy](licenses/DINOv3-LICENSE.md)) | Crown embeddings for identification and health; weights downloaded with an approved Hugging Face account, not redistributed |
| DINOv2-base (facebook/dinov2-base) | Apache-2.0 | Model trial only |
| CLIP ViT-L/14 (openai/clip-vit-large-patch14) | MIT | Model trial only |
| DeepForest (weecology/deepforest-tree) | MIT | Model trial only (rejected) |

**DINOv3 obligations.** Built with DINOv3.
- Access is gated: every operator of the pipeline or a compute node needs their own Hugging Face request approved by Meta.
- Use must comply with trade controls. Military, warfare, nuclear, espionage and weapons uses are prohibited, and the licensee may not permit others such uses.
- Published research results obtained with DINOv3 must acknowledge it; cite arXiv:2508.10104.
- [`models/identification_reference.joblib`](models/identification_reference.joblib) is a classifier trained on DINOv3 features. To the extent it is a derivative work, it is made available under the DINOv3 License, with a copy of that licence in [`licenses/`](licenses/), and not under FarmWings' own licence.

## Python pipeline and compute node

Installed by each operator from PyPI/GitHub; FarmWings does not redistribute them.

| Package | Licence | Package | Licence |
|---|---|---|---|
| numpy | BSD-3-Clause | torch | BSD-3-Clause ¹ |
| scipy | BSD-3-Clause ² | torchvision | BSD-3-Clause ³ |
| rasterio (bundles GDAL, PROJ: MIT) | BSD-3-Clause ⁴ | transformers | Apache-2.0 |
| geopandas | BSD-3-Clause | huggingface_hub | Apache-2.0 |
| shapely (bundles GEOS) | BSD-3-Clause ⁴ | SAM-2 | Apache-2.0 AND BSD-3-Clause |
| pyproj | MIT | fastapi | MIT |
| opencv-python | MIT (wheel), Apache-2.0 (OpenCV) ⁵ | uvicorn | BSD-3-Clause |
| scikit-learn | BSD-3-Clause | python-multipart | Apache-2.0 |
| pandas | BSD-3-Clause | google-auth | Apache-2.0 |
| matplotlib | Matplotlib License (PSF-based) ⁶ | joblib | BSD-3-Clause |
| Pillow | MIT-CMU | deepforest (trial only) | MIT |

The binary wheels carry extra notices. These matter only if you **redistribute** them, for example in a
compute-node container image. Collect the notices below into that image.

1. torch CUDA builds contain NVIDIA CUDA/cuDNN (NVIDIA CUDA EULA / cuDNN SLA), Intel oneMKL (Intel Simplified Software License) and Intel OpenMP (Intel EULA for Developer Tools).
2. scipy bundles Qhull (Qhull licence) and other permissive code (LICENSES_bundled.txt).
3. torchvision bundles libjpeg, libpng, libwebp, zlib, the CPython runtime (PSF-2.0) and NVIDIA cudart/nvjpeg.
4. GEOS: LGPL-2.1-or-later. rasterio wheels also bundle GNU libiconv (LGPL), spatialite and freexl (MPL-1.1 / GPL-2.0+ / LGPL-2.1+) and Qhull.
5. opencv-python statically links Intel IPP ICV (Intel Simplified Software License) and bundles FFmpeg (LGPL-2.1-or-later); Linux wheels also bundle Qt 5 (LGPL).
6. matplotlib statically links FreeType (FreeType License) and Qhull.
