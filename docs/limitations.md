# Limitations and uncertainty

**Detection**
* No field-verified plant list exists. Agreement with the project installation record
  (99.1% / 98.7%) and visual audits are the evidence; neither is ground truth.
* A planted sapling is defined as "on a visible drip line, at a planting-rhythm position".
  Where a drip line is buried or missing, a surviving sapling there is reported as
  between-line vegetation.
* Empty spots are inferred only for gaps of 1.5–4.5 plant spacings; longer gaps (cross lanes,
  line ends) are not filled.
* Plant outlines cover plant plus pit core. `canopy_area_m2` (NDVI-green pixels) is the
  better measure of living canopy.
* `detection_confidence` is a heuristic, not a probability.

**Identification** *(experimental)*
* The model separates planted stock from other vegetation (spatial-CV AUC 0.991). The species
  name comes from the installation record; at sapling size the imagery cannot confirm species.
* Labels come from the planting layout, so label noise (weeds in pits, dead saplings) carries
  into the model. A real species model needs ~100–300 field-verified plants per species.

**Health** *(experimental)*
* Unsupervised and relative: condition groups describe this survey on this date. They are not
  absolute health states and cannot be compared across surveys without radiometric
  calibration.
* "No green canopy" can mean dry, dormant, heavily browsed or dead. It flags plants for a
  field check. It is **not** a disease diagnosis.
* The NDVI sensor, band definitions and calibration are unknown (no metadata in the file).

**Imports and compute**
* Surveys must be GeoTIFFs in a metric CRS. Very small areas (< ~40 m across) contain too
  few plants for the health model; run them with health switched off.
* Processing needs an NVIDIA GPU (≥ 8 GB). The web app itself runs without a server; uploads
  need a running FarmWings compute node.
* The web app is static and the bundled survey data is public. Its sign-in (Google or demo
  access) decides which screens open; it does not protect data. Surveys that must stay private
  are kept on a compute node, which verifies Google sign-in itself.
