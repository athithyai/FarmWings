# Processing pipeline

```
python processing/run_pipeline.py --input "C:\Users\great\Documents\Lab\Hari\Pilot"
python processing/run_pipeline.py --from identify      # resume from a stage
python processing/run_pipeline.py --only health        # one stage
python processing/detect_plants.py --positions-only    # redo planted/between assignment only
```

Run with the project venv (`.venv\Scripts\python.exe`). It layers on the conda env
`construction` (CUDA torch 2.11, rasterio 1.4, SAM 2, transformers) and adds DeepForest for
the trial. A GPU is used for SAM 2.1 and the embedding models. A full run takes about
15 minutes on an RTX 5070 Laptop GPU.

| # | Stage | Script | Main outputs (`processing_outputs/`) | Time |
|---|---|---|---|---|
| 0 | Inspect | `inspect_data.py` | `inspect/inventory.json`, RGB / NDVI previews | 50 s |
| 1 | Align + co-register | `align_rasters.py` | `aligned/rgb_aligned.tif`, `ndvi_aligned.tif`, `ndvi_coreg.tif`, `shift_field.json` | 90 s |
| 2a | Planting lines | `detect_lines.py` | `lines/planting_lines.geojson`, `lines_model.npz`, `line_evidence_rot.npy` | 15 s |
| 2b | Plant detection | `detect_plants.py` | `detection/plants_raw.geojson`, `plants.geojson`, `detection_preview.png` | 7 min |
| 3 | Identification | `identify_plants.py` | `identification/plant_identification.geojson`, `identification_summary.json`, `identification_examples.png` | 1–3 min |
| 4 | Health | `assess_health.py` | `health/plant_health.geojson`, `health_summary.json`, `health_preview.png` | 20 s |
| 5 | Export | `export_results.py` | `web/combined_plants.geojson`, `public/data/{plants,lines,summary}.json` | 5 s |
| 6 | Web tiles | `make_tiles.py` | `public/data/tiles/` (packed WebP XYZ + `index.json`) | 2 min |

Stages 3 and 4 each read only the detection output plus the imagery. Neither reads the
other's result, so the two models stay independent. Stage 5 is the only place they meet.

## Data flow

```
RGB 6 mm ─┐                         ┌─ plant crops (6 mm) ──► Identification ─┐
          ├─► aligned 2.36 cm grid ─┤                                          ├─► combined dataset ─► web
NDVI ─────┘   + NDVI co-registration├─► drip lines ─► candidates ─► SAM 2.1 ─┤
                                    └─ crown NDVI / RGB zonal stats ─► Health ┘
```

## Web data

* `public/data/plants.json`: 6 654 plant polygons (EPSG:4326, simplified to 2 cm) with
  detection, identification and health attributes. It drives the map, plant panel and
  client-side statistics.
* `public/data/lines.json`: visible drip-line stretches.
* `public/data/summary.json`: lifecycle, statistics, model provenance, evaluation results.
* `public/data/tiles/`: RGB (z16–23, from the original 6 mm mosaic), NDVI and vegetation
  mask (z16–22). 256 px WebP tiles are packed into 8 × 8-tile chunk files. The viewer serves
  them through a MapLibre custom protocol (`fw://`), so there is no tile server and only
  ~110 files. Total ~26 MB.

## Frontend

```
npm install
npm run build        # -> dist/ (relative paths; works under any sub-path)
npm run preview      # http://localhost:4173
```

Vite + MapLibre GL JS 6, with no UI framework and no external tile or basemap services.
All selection statistics are computed in the browser.

## Reproducibility notes

* Every result carries `*_model`, `*_model_version` and `processing_date`.
  `summary.json` holds the parameters and evaluation metrics.
* Caches in `processing_outputs/cache/` (crops, embeddings) are invalidated automatically
  when the plant set changes.
* Large rasters and arrays are git-ignored. Regenerate them with the pipeline.
