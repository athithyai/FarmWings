# Model evaluation and selection

Every candidate was tested on the Pilot imagery itself: on representative tiles before
full-raster processing, and with spatial cross-validation or visual audits where no labels
exist. Trial scripts are in `processing/experiments/`.

The scene: *Rhanterium epapposum* saplings, mostly 10–30 cm across, in planting pits along
drip lines 2 m apart, on bright desert sand with spontaneous annual vegetation between rows.
Imagery: RGB 6 mm, NDVI 2.4 cm. This scale rules out most published tree and palm models.

## 1. Plant detection

| Candidate | Result on test tiles | Decision |
|---|---|---|
| DeepForest (`weecology/deepforest-tree`, RetinaNet, ~10 cm tree crowns) | **0 detections** at 2.4 cm and 10 cm (score > 0.1) | rejected: wrong object scale |
| detectree2, YOLO / RetinaNet palm detectors | not run: trained on multi-metre crowns, no sapling weights | rejected on relevance |
| SAM 2.1 automatic mask generation | ~200 unselective masks per 24 m tile (soil, halos, pipe) | rejected as a detector |
| **RGB + NDVI candidates → SAM 2.1 prompts** | outlines follow pits and saplings | **selected** |

**Final method** (`detect_plants.py`, `detect_lines.py`):
1. **Drip lines**: black top-hat of brightness, dominant orientation, rotated column profiles
   per 9.4 m block, tracking. Pilot: 112 drip lines, 1.998 m spacing. A per-location visibility
   score (line vs adjacent soil) keeps only *visible* drip lines as planting lines.
2. **Candidates**: robust z of RGB darkness (drip lines erased by grey closing) + robust z of
   NDVI above local soil; local maxima.
3. **SAM 2.1 hiera-large** outlines each candidate on a **1.18 cm** RGB grid (fine saplings),
   trimmed to plant evidence; a large mask with no plant core is rejected.
4. **NDVI recall pass**: green NDVI patches no object covers (≥ 0.02 m² on a line, ≥ 0.05 m²
   between lines) get their own SAM prompt, or their NDVI outline.
5. **Planting positions**: per line, a dynamic-programming fit of the planting rhythm
   (consecutive plants a whole number of ~2 m spacings apart, ±0.3) picks one object per
   planting spot using its evidence (confidence, NDVI, closeness to the line). Touching
   fragments merge; vegetation between spots is kept as between-line vegetation; drip-pipe
   segments (dark, elongated along the line, not green) are rejected.
6. **Planting rows only**: a drip line holding fewer than half the median number of plants per
   line carries no planting row (boundary and feeder pipes along the block edge); its objects
   are between-line vegetation. Pilot: 5 of 112 drip lines (L002, L005, L086, L108, L110).
7. **Empty spots**: gaps of 1.5–4.5 spacings between planted plants are expected positions. A
   spot is reported empty only when no plant is there: a vegetated object within 0.75 m of the
   spot and 0.6 m of the drip line (saplings are not always exactly on the line) or an
   unoutlined green NDVI patch (NDVI ≥ soil + 0.20, ≥ 0.01 m²) fills it instead.

**Result (Pilot):** 98 planting lines, 5,657 planted saplings, 25 empty spots (5,682 planting
spots; the installation record has 5,684), 2,618 between-line objects.

**Checks.** No field-verified plant list exists. Two independent checks were used:
* The **project installation record** (5,684 planting points, not ground truth, used as a
  reference only): 99.5% of FarmWings plants lie within 0.6 m of a recorded point, 99.0% of
  recorded points have a FarmWings plant, median offset 5.6 cm. Every planting line has the
  same number of planting spots as the record. The rhythm settings gave the
  same result across a sensitivity sweep (F1 0.987–0.990), so the rule is not tuned to one
  setting.
* **Visual audits** of 6 mm crops where the two disagree: about half of the recorded points
  FarmWings leaves empty show no living plant, and most points the reference marks "not
  detected" are planting pits with no green canopy, which FarmWings reports as such.

`detection_confidence` = √(SAM predicted IoU × candidate strength): a heuristic score, not a
calibrated probability.

## 2. Plant identification *(experimental)*

**Candidates:** Pl@ntNet-style close-up classifiers (rejected: ground-level photos), palm
detectors (no sapling weights), CLIP zero-shot, and frozen embeddings + a small classifier
(DINOv2-base, **DINOv3 ViT-L/16 SAT-493M**, CLIP ViT-L/14).

**Labels without hand annotation:** planted stock = on a visible drip line with a clear core;
other vegetation = between lines with an NDVI signal. Position is not a model input, and the
drip line is inpainted out of every crop so the model cannot learn "line = planted".

| Inputs (spatial 5-fold CV, 25 m blocks) | ROC-AUC |
|---|---|
| CLIP ViT-L/14 zero-shot | 0.770 |
| NDVI + RGB indices + geometry only | 0.981 |
| DINOv2-base crown crop + tabular | 0.992 |
| CLIP ViT-L/14 crown crop + tabular | 0.991 |
| **DINOv3 SAT-493M crown crop + tabular** | **0.993** (final plant set: 0.992) |

Selected: DINOv3 SAT-493M crown embedding + NDVI/RGB/geometry → logistic regression. Below
0.7 confidence a plant is Unclassified. When a survey has too few weak labels (no visible
drip lines), the Pilot-trained reference model (`models/identification_reference.joblib`) is
used and the summary says so. The species name comes from the installation record; the
imagery cannot confirm species at sapling size.

## 3. Plant health *(experimental)*

No validated pretrained health model exists for young desert saplings at this resolution
(published models target date-palm leaves, crop rust, or close-range leaf photos), and there
are no field health labels. The health model is therefore **unsupervised**:

* **Green canopy** per plant = pixels with NDVI ≥ 0.20 above the plant's own soil ring
  (0.3–0.8 m). A threshold of 0.08 also counted damp, mulched pit soil; 0.20 was checked on
  6 mm crops (no-canopy plants show dry brown material only).
* **Condition groups**: Gaussian mixture (K = 3–5 by BIC) on the DINOv3 crown embedding (PCA
  16) plus six NDVI/RGB indicators, fitted on **planted saplings only**. Groups are ordered by
  their indicator profile and named Very good … Very poor.

| Group (Pilot) | Plants | Median NDVI | Green canopy | Without green canopy |
|---|---|---|---|---|
| Very good | 868 | 0.36 | 983 cm² | 0% |
| Good | 1,664 | 0.27 | 757 cm² | 0% |
| Fair | 808 | 0.22 | 256 cm² | 13% |
| Poor | 1,346 | 0.21 | 429 cm² | 2% |
| Very poor | 971 | 0.17 | 56 cm² | 45% |

Stability on 80% resamples: ARI 0.81. Agreement with a transparent vigour index (weighted
robust z of the same indicators): Spearman ρ 0.92.

*Plant health is inferred from RGB and NDVI remote-sensing indicators and is not a laboratory
disease diagnosis.*
