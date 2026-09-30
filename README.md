# Farmwings — Drone Mapping & Plant Intelligence

**Hari Pilot.** Plant-level results from one RGB orthomosaic (6 mm) and one NDVI raster
(2.4 cm) of a drip-irrigated planting block (2.66 ha, EPSG:32638).

```
RGB + NDVI → align & co-register → drip-line detection → plant detection (SAM 2.1)
          → Plant Identification Model → Plant Health Model → combined dataset → map viewer
```

| Result | Value |
|---|---|
| Planted positions (on visible drip lines) | **5 708** on 112 lines, 2.0 m spacing |
| Between-line vegetation | 946 |
| Identified | 98 % — Palm (planted) 5 458 · Other vegetation 1 085 · Unclassified 111 |
| Identification model | DINOv3 ViT-L SAT-493M crown embedding + NDVI/RGB → logistic regression; spatial-CV AUC 0.993 *(experimental)* |
| Health model | relative multimodal vigour index (NDVI contrast, NDVI, VARI, green cover) *(experimental)* |
| High / low vigour (planted) | 35 % high or very high · 1 699 low or very low |

Three separate stages, three separate map layers:
1. **Detection**: where the plants are (`detection_confidence`, area, line).
2. **Identification**: what plant it is (`plant_class`, `identification_confidence`).
3. **Health**: its vegetation condition (`health_class`, `health_score`, NDVI / RGB indicators).

*Plant health is inferred from RGB and NDVI remote-sensing indicators and is not a
laboratory disease diagnosis.*

## Viewer

MapLibre, map-first: RGB, NDVI and vegetation-mask imagery; detected plants, planting
lines, identification and health layers; a plant detail panel; polygon, rectangle or view
selection with in-browser statistics; field analytics; model provenance.

```
npm install
npm run build
npm run preview        # http://localhost:4173
```

**Deployment is private by decision.** The imagery is client project data. The GitHub
repository is private; `.github/workflows/deploy-pages.yml` builds on every push but only
publishes to GitHub Pages if the repository is made public. The shareable build is
published as a private link controlled by the owner.

## Processing

```
python processing/run_pipeline.py --input "C:\Users\great\Documents\Lab\Hari\Pilot"
```

See [docs/processing.md](docs/processing.md). Dependencies: [requirements.txt](requirements.txt)
(GPU recommended for SAM 2.1 and the embedding models).

## Documentation

* [docs/data-inventory.md](docs/data-inventory.md): files, CRS, grids, NDVI range, alignment
* [docs/model-evaluation.md](docs/model-evaluation.md): models tested, rejected and chosen, with scores
* [docs/processing.md](docs/processing.md): pipeline stages and outputs
* [docs/limitations.md](docs/limitations.md): what the results do and do not mean
* [docs/architecture-future.md](docs/architecture-future.md): path to API, storage, PostGIS, orchestration

## Layout

```
app/frontend/        Vite + MapLibre viewer (index.html, src/)
processing/          pipeline stages + experiments/ (model trials)
processing_outputs/  stage outputs (large rasters git-ignored; summaries and previews kept)
public/data/         web data: plants.json, lines.json, summary.json, tiles/
docs/                documentation
```
