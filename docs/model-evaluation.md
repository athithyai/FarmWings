# Model evaluation and selection

Every candidate was tested on the Hari imagery itself: on representative tiles before
full-raster processing, and with spatial cross-validation where labels exist. Trial
scripts are in `processing/experiments/`, with outputs in `processing_outputs/experiments/`.

The scene: 0.2–0.9 m seedlings in planting pits at **6 mm RGB / 2.4 cm NDVI**, on bright
desert sand, along drip lines 2 m apart. This scale and domain rule out most published
tree and palm models.

## 1. Plant detection / segmentation

| Candidate | What it is | Test on 3 × 24 m tiles | Decision |
|---|---|---|---|
| **DeepForest** (`weecology/deepforest-tree`, RetinaNet) | pretrained tree-crown detector (NEON, ~10 cm) | **0 detections** at native 2.4 cm and resampled 10 cm, even at score > 0.1 | rejected: trained on multi-metre tree crowns |
| detectree2 (Mask R-CNN) | tropical canopy crowns | not run: needs detectron2 (fragile on Windows), same crown-scale mismatch | rejected on relevance |
| YOLO/RetinaNet palm detectors (e.g. `ckn3/palm-ds-sp`, UAE Al Ain palm dataset) | mature date / oil palm crowns, 3–10 m | not run: no public weights for seedlings, wrong object scale | rejected on relevance |
| **SAM 2.1 automatic mask generation** (hiera-large) | promptless instance masks | 191–226 masks per tile, mostly soil patches, pit halos and drip-line fragments; unselective | rejected as a detector |
| GroundingDINO + SAM | text-prompted boxes | cached locally but not pursued; a text prompt ("plant") has no advantage over a physically grounded RGB+NDVI candidate at this scale | not needed |
| **RGB+NDVI candidates → SAM 2.1 point+box prompts** | chosen | 193–253 objects per tile, masks follow pits and plants | **selected** |

**Chosen method** (`detect_plants.py`):
1. A "plant-ness" surface: robust z of RGB darkness (drip lines erased by a 7 px grey
   closing) plus robust z of NDVI anomaly, both relative to local soil (σ ≈ 0.95 m).
2. Local maxima become candidates.
3. SAM 2.1 hiera-large is prompted with each point plus a 1.1 m box.
4. Each mask is trimmed to its plant/pit core. Shape filters and overlap suppression follow.

**Planting lines** (`detect_lines.py`): black top-hat, dominant orientation, rotated column
profiles per 9.4 m block, then tracking. This yields 112 lines at 1.998 m spacing. A
per-location **visibility score** (line vs adjacent soil) decides whether a drip line is
actually visible. A detection is a *planted position* only if it sits within 0.31 m of a
line **and** that line is locally visible. This follows the project instruction that the
visible guide lines are the planting lines. Rows of pits or vegetation where no drip line
is visible are not planting lines. Checked visually on crops in every score band: every
plant scoring ≥ 5 has a visible drip line through it; below 5 there is none, and that band
includes white plastic debris.

Result: 6 654 objects, of which **5 708 are planted positions** and 946 are between-line
vegetation. 100 objects were dropped (no drip line and no vegetation signal). Median crown
area is 0.18 m², and 92 % have a detected plant/pit core.

`detection_confidence` = √(SAM predicted IoU × candidate strength). It is a heuristic
score, **not a calibrated probability**, because there are no annotated plants to
calibrate against.

## 2. Plant Identification Model

**Question:** what plant is this? The project declared the planted species as palm.

**Candidates considered**
* Pl@ntNet and other close-up plant-photo classifiers: rejected on domain (ground-level
  photos of leaves and flowers, not top-down 6 mm crowns).
* Palm detectors / classifiers: trained on mature crowns; no seedling weights.
* **CLIP ViT-L/14 zero-shot** ("aerial photo of a young palm seedling planted in a pit" vs
  "small wild weeds and grass on sand"): tested.
* **Frozen embeddings + small classifier** (Option B): DINOv2-base (natural images),
  **DINOv3 ViT-L/16 SAT-493M** (satellite-pretrained remote-sensing foundation model),
  CLIP ViT-L/14 image tower. All are cached locally and run on the RTX 5070.
* RemoteCLIP, TerraMind and other EO foundation models: designed for 0.3–10 m satellite
  scenes and multispectral stacks, not 6 mm single-plant crops. The satellite-pretrained
  DINOv3 was tested as the representative of this family.

**Labels.** There are no annotated plants, so labels come from the planting design, which
is independent of plant appearance. Planted stock = on a visible drip line with a clear
core and a strong candidate (5 123). Spontaneous vegetation = between lines with a
vegetation signal (946). **Position is not a model input.**

The first trial exposed a leak. Raw crops show the drip line through every planted plant,
so a model could learn "line = planted". To remove it, crown crops inpaint the drip line
(oriented line-kernel opening) and replace everything outside the dilated crown with sand
colour.

**Spatial 5-fold cross-validation** (25 m blocks, final plant set):

| Inputs | ROC-AUC | Balanced accuracy |
|---|---|---|
| CLIP ViT-L/14 zero-shot | 0.770 | 0.564 |
| NDVI + RGB indices + geometry only | 0.981 | 0.943 |
| DINOv2-base, raw crop (+ tabular) | 0.995 | 0.975 |
| CLIP ViT-L/14, raw crop (+ tabular) | 0.994 | 0.973 |
| DINOv3 SAT-493M, raw crop (+ tabular) | 0.990 | 0.967 |
| DINOv2-base, crown crop + tabular | 0.992 | 0.974 |
| CLIP ViT-L/14, crown crop + tabular | 0.991 | 0.970 |
| **DINOv3 SAT-493M, crown crop + tabular** | **0.993** | **0.980** |

Raw-crop scores are optimistic because of the drip-line leak. **Selected: DINOv3 SAT-493M
crown-crop embedding + NDVI/RGB/geometry → logistic regression.** It is the best leak-free
configuration. Labelled plants get their out-of-fold probability as confidence; below 0.7
a plant is **Unclassified**.

**Output:** Palm (planted) 5 458 · Other vegetation 1 085 · Unclassified 111 (98.3 %
identified). On planting lines: 5 436 palm, 177 other vegetation (weeds or dead material
in pits), 95 unclassified.

**What this does and does not show.** The model separates planted stock from spontaneous
vegetation reliably. It cannot confirm the species "palm": at 0.2–0.9 m crowns the
seedlings are not distinguishable to species from above, and no labelled species data
exists. The class name carries the project's declared species. See `limitations.md` for
the labels needed to make this a true species model.

## 3. Plant Health Model

**Question:** what is the vegetation condition of this plant?

**Candidates considered**
* Published UAV health / stress classifiers (date-palm leaf-disease CNNs, NDVI + fully
  connected crop-rust models, RGB→NDVI regressors): trained on other crops, mature trees,
  or close-range leaf imagery. None has public weights for desert seedlings at this
  resolution, and none is validated here. Rejected, which leads to **Approach B**.

**Approach B: relative multimodal vigour model** (`assess_health.py`). Six indicators,
each robust-z scored (median / MAD over all detected plants) and then weighted:

| Indicator | Weight | Why |
|---|---|---|
| NDVI contrast = crown median − soil-ring median (0.3–0.8 m ring) | 0.30 | removes soil / moisture variation across the field |
| crown p90 NDVI | 0.20 | greenest tissue; crowns include pit soil |
| crown median NDVI | 0.15 | overall greenness |
| VARI (RGB, median) | 0.15 | independent RGB greenness |
| green fraction (ExG > 0.02) | 0.10 | share of crown that is green |
| log green area | 0.10 | vigour scales with living canopy size |

The composite is re-standardised; `health_score = Φ(z)`. The classes are robust-z bands
(≥ 1.5 / 0.5 / −0.5 / −1.5), not arbitrary NDVI cut-offs and not forced quintiles.

**Internal consistency:** the NDVI-only sub-score and the RGB-only sub-score (two
independent sensors) agree at Spearman ρ = 0.76.

**Planted positions:** Very high 621 · High 1 367 · Moderate 2 021 · Low 1 439 ·
Very low 260. Mean crown NDVI is 0.254, against a soil median of 0.099.

*Plant health is inferred from RGB and NDVI remote-sensing indicators and is not a
laboratory disease diagnosis.*
