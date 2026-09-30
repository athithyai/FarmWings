# Future architecture (documentation only)

Today's pilot is a static site. The pipeline runs locally and writes `public/data`, and the
viewer computes everything client-side. This is how it evolves into a product without
rewriting the core: each processing stage already has a single input and a single output.

```
External mapping platform (e.g. drone operator / DroneDeploy / Pix4D exports)
        ↓  webhook or scheduled pull
FarmWings API  (auth, projects, surveys, jobs)
        ↓
User / organisation access  (orgs → projects → surveys; roles: owner, analyst, viewer)
        ↓
Drone capture  (RGB + multispectral orthomosaics, flight metadata)
        ↓
Cloud object storage  (S3 / Azure Blob / GCS: raw/, cogs/, derived/, tiles/)
        ↓
Processing orchestration  (job queue; one task per pipeline stage, idempotent, resumable)
        ↓
Plant detection model        ─┐
        ↓                     │  versioned models from the registry,
Plant identification model    │  each writing its own table
        ↓                     │
Plant health model           ─┘
        ↓
PostGIS / geospatial services  (plants, lines, surveys; vector tiles; spatial queries)
        ↓
FarmWings viewer  (same UI; data from the API instead of static JSON)
```

## Storage
* S3, Azure Blob or GCS, behind one storage interface. Rasters become Cloud-Optimised
  GeoTIFFs (the source files today are strip-organised and have no overviews, which is
  slow to window).
* Web imagery: PMTiles or COG served by range requests. Today's packed-tile protocol is a
  small step towards that.

## Processing
* Each `processing/*.py` stage becomes a containerised task with the same CLI. The
  orchestrator (e.g. a queue plus workers, or Argo / Prefect) passes storage URIs.
* CPU workers: alignment, lines, zonal statistics, tiling. GPU workers (autoscaled, spot
  or preemptible): SAM 2.1 and embedding models. Tiles are processed in parallel with
  overlap and a de-duplication step, as today.

## Data model (PostGIS)
* `survey(id, project_id, date, sensor, gsd, footprint)`
* `plant(id, project_id, geom_point, first_seen_survey)`: a stable identity across surveys,
  matched by position along a planting line.
* `detection(plant_id, survey_id, geom, confidence, model_version)`
* `identification(plant_id, survey_id, class, confidence, model_version)`
* `health(plant_id, survey_id, class, score, ndvi_stats jsonb, rgb_stats jsonb, model_version)`
* Spatial selection statistics move from the browser to SQL (`ST_Within` plus aggregates)
  or to vector-tile attributes.

## Repeated surveys
* Plant IDs persist across flights, which gives survival rate, growth (green area), vigour
  trend and replacement lists per planting line.
* The health index moves from "relative to this field today" to "change since the last
  survey" once radiometric calibration (reflectance panels / sensor metadata) is available.

## Models
* A model registry (e.g. MLflow) versions detector prompts and parameters, embedding
  backbones, classifier weights and health weights. Each output row references a model
  version, as the pilot already does.
* Active learning: analysts confirm or correct plants in the viewer, and those labels
  retrain the identification classifier and calibrate detection confidence.

## Access, integrations, operations
* Auth (OIDC), organisation and role management, per-project sharing links.
* External integrations: import from mapping platforms; export to GeoPackage, Shapefile and
  CSV; webhook out when a survey finishes processing.
* CI/CD: lint and tests on the pipeline, a golden-tile regression test for each model
  version, and container builds and deploys.
* Monitoring: job duration and failure rate, GPU utilisation, model drift (class and
  confidence distributions per survey).
* Notifications: "survey processed", "N plants dropped to low vigour", "gaps detected on
  line L042", by email or chat.
