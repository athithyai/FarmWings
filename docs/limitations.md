# Limitations and uncertainty

**Detection**
* No hand-annotated plants exist, so detection precision and recall are **not measured**.
  Quality was judged visually on representative tiles and the full-field previews.
  `detection_confidence` is a heuristic (SAM mask IoU × candidate strength), not a
  probability.
* A planted position is defined as "on a visible drip line". Where a drip line is buried
  or missing, a surviving seedling in that stretch is reported as between-line vegetation.
  Where a line is visible, an empty pit or dead seedling is still detected as a planting
  position (usually with low vigour). A few pits hold weeds rather than the planted stock.
* Masks cover plant plus pit core. "Crown area" therefore means *plant/pit object area*,
  not leaf area. `green_area_m2` (area × green fraction) is the better canopy proxy.
* Dense patches of spontaneous vegetation are segmented as clumps, not as individual plants.

**Identification** (Experimental)
* The model separates **planted stock** from **spontaneous vegetation** (spatial-CV ROC-AUC
  0.993). The label "Palm" is the species **declared by the project**. At 0.2–0.9 m seedling
  crowns and 6 mm GSD, the imagery cannot confirm species.
* Training labels are weak (derived from the planting layout). Label noise, such as weeds
  in pits or dead seedlings, carries into the model.
* To make this a real species model you would need: ~100–300 GPS-tagged, field-verified
  plants per species (including non-palm species planted in the block, if any), plus
  examples of dead or empty pits, collected across the field and survey dates. The same
  embedding + classifier pipeline accepts those labels unchanged.

**Health** (Experimental)
* It is a **relative** index: "vigour compared with the other plants in this field on this
  date". It is not an absolute health state and cannot be compared across surveys without
  radiometric normalisation.
* Low NDVI can mean stress, a small or young plant, dormancy, dry mulch in the pit, or a
  dead or missing seedling. **It is not a disease diagnosis.** No field validation data
  exists yet.
* The NDVI sensor, band definitions and calibration are unknown (no metadata in the file).
  Absolute NDVI values should not be compared with other sensors.
* The RGB and NDVI residual misregistration (median 4.8 cm, max 7 cm) was corrected with a
  smooth shift field. Small plants can still mix crown and soil pixels.

**Data**
* One survey date, with no acquisition metadata (date, altitude, sensor).
* The imagery is client project data. The deployment is private by decision (see README).
