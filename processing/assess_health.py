"""Stage 4 - Plant Health Model  ("what is the condition of this plant?").

Separate from detection and identification: it reads only the detected plant polygons and
the imagery - never the identification result.

No validated pretrained health / stress model exists for young desert saplings at 6 mm -
2.4 cm GSD, and no field health labels exist, so the model is UNSUPERVISED:

  per plant
    * DINOv3 ViT-L/16 SAT-493M embedding of the crown-focused 6 mm RGB crop (same crops as
      the identification stage; computed here independently), reduced by PCA to 16 dims
    * six NDVI / RGB condition indicators, robust-z scored against the field:
        ndvi_contrast (crown median NDVI - soil ring 0.3-0.8 m), p90_ndvi, median_ndvi,
        VARI, green fraction (ExG > 0.02), log green area
  -> both blocks standardised and weighted equally
  -> Gaussian mixture model, K = 3..5 chosen by BIC (5 restarts each)
  -> groups ORDERED by their mean indicator profile (vigour composite) and named by rank
     (Very good ... Very poor condition); each group also carries a measured profile
     (median NDVI, NDVI contrast, green cover, green area) so the name can be checked
  per plant: health_class (group name), health_confidence (posterior of that group),
  health_score (posterior-weighted group score in [0, 1])

The previous transparent vigour index (weighted robust z of the same six indicators) is kept
as secondary fields vigour_class / vigour_score, and used to check the model (Spearman).

Plant health is inferred from RGB and NDVI remote-sensing indicators and is not a
laboratory disease diagnosis.

Outputs (processing_outputs/health/): plant_health.geojson, health_summary.json,
health_preview.png, health_groups.png
"""
from __future__ import annotations

from common import DEFAULT_INPUT, OUT, PROCESSING_DATE, out_dir, write_json  # noqa: I001 (sets PROJ_DATA first)

import numpy as np
from scipy.stats import norm, spearmanr

HEALTH_MODEL = "Unsupervised condition model: Gaussian mixture on DINOv3-SAT crown embedding + NDVI/RGB indicators"
HEALTH_VERSION = "0.2.0"
DISCLAIMER = ("Plant health is inferred from RGB and NDVI remote-sensing indicators and is not a "
              "laboratory disease diagnosis.")
RING_INNER_M, RING_OUTER_M = 0.3, 0.8
CANOPY_DNDVI = 0.20          # canopy pixel: NDVI at least this much above the plant's soil ring
                             # (0.08 also counted damp, mulched pit soil; checked on 6 mm crops)
LIVING_MIN_CANOPY_M2 = 0.004 # ~7 cm across: smallest canopy counted as a living plant
PIXEL_AREA_M2 = 0.0236 ** 2
K_RANGE = range(3, 6)
PCA_DIMS = 16
SEED = 0

# vigour index (secondary indicator, also used to order the groups)
WEIGHTS = {"ndvi_contrast": 0.30, "p90_ndvi": 0.20, "median_ndvi": 0.15,
           "vari": 0.15, "green_fraction": 0.10, "log_green_area": 0.10}
NDVI_SIDE = ["ndvi_contrast", "p90_ndvi", "median_ndvi"]
RGB_SIDE = ["vari", "green_fraction", "log_green_area"]
VIGOUR_BANDS = [(1.5, "Very high vigour"), (0.5, "High vigour"), (-0.5, "Moderate vigour"),
                (-1.5, "Low vigour"), (-np.inf, "Very low vigour")]
GROUP_NAMES = {
    3: ["Good condition", "Fair condition", "Poor condition"],
    4: ["Very good condition", "Good condition", "Fair condition", "Poor condition"],
    5: ["Very good condition", "Good condition", "Fair condition", "Poor condition", "Very poor condition"],
}


def robust_z(x: np.ndarray) -> np.ndarray:
    med = np.nanmedian(x)
    mad = np.nanmedian(np.abs(x - med)) * 1.4826
    return (x - med) / (mad if mad > 0 else np.nanstd(x) + 1e-9)


def vigour_band(z: float) -> str:
    for thr, name in VIGOUR_BANDS:
        if z >= thr:
            return name
    return VIGOUR_BANDS[-1][1]


def zonal_indicators(gdf):
    import pandas as pd
    from features import ndvi_stats, plant_pixels, rgb_indices
    print("zonal statistics: crowns ...")
    nds, rgbs = plant_pixels(gdf)
    print("zonal statistics: soil rings ...")
    rings = [g.buffer(RING_OUTER_M).difference(g.buffer(RING_INNER_M)) for g in gdf.geometry]
    ring_nd, _ = plant_pixels(gdf, geoms=rings, exclude_geoms=gdf.geometry)
    rows = []
    for v, px, rv, area in zip(nds, rgbs, ring_nd, gdf.area_m2.values):
        s = ndvi_stats(v) | rgb_indices(px)
        bg = float(np.median(rv)) if rv.size >= 20 else np.nan
        s["bg_ndvi"] = bg
        s["ndvi_contrast"] = (s["median_ndvi"] - bg) if s["median_ndvi"] is not None else np.nan
        s["p90_contrast"] = (s["p90_ndvi"] - bg) if s["p90_ndvi"] is not None else np.nan
        gf = s["green_fraction"] if s["green_fraction"] is not None else 0.0
        s["green_area_m2"] = float(area * gf)
        # living canopy: crown pixels clearly greener (NDVI) than the plant's own soil ring
        s["canopy_area_m2"] = float(((v - bg) >= CANOPY_DNDVI).sum() * PIXEL_AREA_M2) if v.size and np.isfinite(bg) else 0.0
        s["log_green_area"] = float(np.log(area * gf + 1e-3))
        rows.append(s)
    f = pd.DataFrame(rows)
    f["bg_ndvi"] = f.bg_ndvi.fillna(f.bg_ndvi.median())
    f["ndvi_contrast"] = f.ndvi_contrast.fillna(f.median_ndvi - f.bg_ndvi)
    f["p90_contrast"] = f.p90_contrast.fillna(f.p90_ndvi - f.bg_ndvi)
    return f


def crown_embeddings(gdf, input_dir):
    from identify_plants import DEFAULT_BACKBONE, embed, load_crops, load_crown_crops
    crops = load_crops(gdf, input_dir)
    emb = embed(load_crown_crops(gdf, crops), DEFAULT_BACKBONE, tag="_crown")
    return emb, crops


def fit_groups(Zi: np.ndarray, emb: np.ndarray):
    """Returns (gmm, X, k, bic_by_k, pca_explained)."""
    from sklearn.decomposition import PCA
    from sklearn.mixture import GaussianMixture
    from sklearn.preprocessing import StandardScaler
    E = StandardScaler().fit_transform(emb)
    pca = PCA(n_components=PCA_DIMS, random_state=SEED).fit(E)
    Ep = pca.transform(E)
    Ep = Ep / Ep[:, 0].std()                         # keep the PCA spectrum, unit scale on PC1
    # equal weight for the two blocks: each block's total variance scaled to 1
    Ib = Zi / np.sqrt((Zi.var(0)).sum())
    Eb = Ep / np.sqrt((Ep.var(0)).sum())
    X = np.c_[Ib, Eb]
    bic = {}
    models = {}
    for k in K_RANGE:
        g = GaussianMixture(k, covariance_type="full", n_init=5, random_state=SEED, reg_covar=1e-4).fit(X)
        bic[k] = float(g.bic(X))
        models[k] = g
    k = min(bic, key=bic.get)
    return models[k], X, k, bic, float(pca.explained_variance_ratio_.sum())


def stability(X: np.ndarray, k: int, labels: np.ndarray, n=5):
    """Mean adjusted Rand index between the final grouping and refits on 80 % subsamples."""
    from sklearn.metrics import adjusted_rand_score
    from sklearn.mixture import GaussianMixture
    rng = np.random.default_rng(SEED)
    aris = []
    for i in range(n):
        idx = rng.choice(len(X), int(0.8 * len(X)), replace=False)
        g = GaussianMixture(k, covariance_type="full", n_init=2, random_state=100 + i, reg_covar=1e-4).fit(X[idx])
        aris.append(adjusted_rand_score(labels, g.predict(X)))
    return float(np.mean(aris)), float(np.min(aris))


def main(input_dir=DEFAULT_INPUT) -> dict:
    import geopandas as gpd
    import pandas as pd

    out = out_dir("health")
    gdf = gpd.read_file(OUT / "detection" / "plants.geojson")
    f = zonal_indicators(gdf)
    # Health is assessed for PLANTED saplings (detected planting positions). Field statistics
    # and the condition groups come from that population; between-line vegetation (weeds,
    # annuals) keeps its NDVI / RGB statistics but gets no health grade.
    planted = gdf.on_planting_line.values.astype(bool)

    def rz(x):
        ref = x[planted]
        med = np.nanmedian(ref)
        mad = np.nanmedian(np.abs(ref - med)) * 1.4826
        return (x - med) / (mad if mad > 0 else np.nanstd(ref) + 1e-9)

    Z = pd.DataFrame({k: rz(f[k].astype(float).values) for k in WEIGHTS}).clip(-6, 6).fillna(0.0)
    vig_comp = (sum(Z[k] * w for k, w in WEIGHTS.items())).values
    vig_z = rz(vig_comp)
    ndvi_sub = rz(sum(Z[k] for k in NDVI_SIDE).values)
    rgb_sub = rz(sum(Z[k] for k in RGB_SIDE).values)

    print("crown embeddings (DINOv3-SAT) ...")
    emb, crops = crown_embeddings(gdf, input_dir)
    gmm, X, k, bic, pca_var = fit_groups(Z[list(WEIGHTS)].values[planted], emb[planted])
    post = gmm.predict_proba(X)
    raw_lab = post.argmax(1)
    vz = vig_z[planted]

    # order groups by their mean vigour composite (NDVI / colour profile), best first
    means = np.array([vz[raw_lab == j].mean() if (raw_lab == j).any() else -99 for j in range(k)])
    order = np.argsort(-means)
    rank_of = {int(j): r for r, j in enumerate(order)}
    names = GROUP_NAMES[k]
    group_score = norm.cdf(means)
    lab = np.array([rank_of[int(j)] for j in raw_lab])
    score = post @ group_score
    conf = post.max(1)
    ari_mean, ari_min = stability(X, k, raw_lab)

    fp = f[planted].reset_index(drop=True)
    profiles = []
    for r, j in enumerate(order):
        m = raw_lab == j
        profiles.append({
            "rank": r + 1, "name": names[r], "n_plants": int(m.sum()),
            "median_ndvi": float(np.nanmedian(fp.median_ndvi[m])),
            "ndvi_contrast": float(np.nanmedian(fp.ndvi_contrast[m])),
            "green_fraction": float(np.nanmedian(fp.green_fraction[m])),
            "canopy_area_m2": float(np.nanmedian(fp.canopy_area_m2[m])),
            "no_green_canopy_share": float((fp.canopy_area_m2[m] < LIVING_MIN_CANOPY_M2).mean()),
            "vari": float(np.nanmedian(fp.vari[m])),
            "group_score": float(group_score[j]),
        })
    for p_ in profiles:
        p_["profile"] = (f"median NDVI {p_['median_ndvi']:.2f} ({p_['ndvi_contrast']:+.2f} vs soil), "
                         f"green canopy {p_['canopy_area_m2'] * 1e4:.0f} cm², "
                         f"{p_['no_green_canopy_share'] * 100:.0f}% without green canopy")

    n = len(gdf)
    h_class = np.full(n, None, dtype=object)
    h_group = np.full(n, None, dtype=object)
    h_conf = np.full(n, np.nan)
    h_score = np.full(n, np.nan)
    h_class[planted] = [names[r] for r in lab]
    h_group[planted] = [f"G{r + 1}" for r in lab]
    h_conf[planted] = conf
    h_score[planted] = score
    living = (f.canopy_area_m2 >= LIVING_MIN_CANOPY_M2).values

    res = gdf[["plant_id", "geometry"]].copy()
    res["health_class"] = h_class
    res["health_group"] = h_group
    res["health_confidence"] = np.round(h_conf, 3)
    res["health_score"] = np.round(h_score, 3)
    res["vigour_class"] = [vigour_band(v) if pl else None for v, pl in zip(vig_z, planted)]
    res["vigour_score"] = np.where(planted, norm.cdf(vig_z), np.nan).round(3)
    res["vigour_z"] = np.where(planted, vig_z, np.nan).round(3)
    for c in ["mean_ndvi", "median_ndvi", "min_ndvi", "max_ndvi", "std_ndvi", "p10_ndvi", "p90_ndvi",
              "bg_ndvi", "ndvi_contrast"]:
        res[c] = f[c].astype(float).round(4)
    res["valid_pixel_count"] = f.valid_pixel_count.astype(int)
    for c in ["exg", "vari", "gli", "rg_ratio", "green_fraction", "brightness"]:
        res[c] = f[c].astype(float).round(4)
    res["green_area_m2"] = f.green_area_m2.round(4)
    res["canopy_area_m2"] = f.canopy_area_m2.round(4)
    res["living_canopy"] = living
    res["canopy_status"] = np.where(living, "Green canopy", "No green canopy (dry, dormant or dead)")
    res["ndvi_subscore_z"] = np.where(planted, ndvi_sub, np.nan).round(3)
    res["rgb_subscore_z"] = np.where(planted, rgb_sub, np.nan).round(3)
    res["health_model"] = HEALTH_MODEL
    res["health_model_version"] = HEALTH_VERSION
    res["health_inputs"] = "DINOv3-SAT crown-crop embedding (PCA 16) + NDVI contrast / NDVI / VARI / green cover / green area"
    res["health_note"] = DISCLAIMER
    res["experimental"] = True
    res["processing_date"] = PROCESSING_DATE
    res.to_file(out / "plant_health.geojson", driver="GeoJSON")

    rho_vig = spearmanr(score, vz).statistic
    rho_sub = spearmanr(ndvi_sub[planted], rgb_sub[planted]).statistic
    summary = {
        "model": HEALTH_MODEL, "version": HEALTH_VERSION,
        "approach": "unsupervised (no validated pretrained model, no field health labels)",
        "population": "planted saplings (detected planting positions); between-line vegetation is not graded",
        "k": int(k), "bic_by_k": bic, "pca_dims": PCA_DIMS, "pca_explained_variance": pca_var,
        "classes": names, "groups": profiles,
        "n_plants": int(planted.sum()),
        "class_counts": {nm: int((lab == r).sum()) for r, nm in enumerate(names)},
        "mean_health_score": float(score.mean()),
        "green_canopy_planted": int(living[planted].sum()),
        "no_green_canopy_planted": int((~living[planted]).sum()),
        "canopy_rule": {"dndvi_above_soil": CANOPY_DNDVI, "min_canopy_m2": LIVING_MIN_CANOPY_M2},
        "median_canopy_m2_planted": float(np.median(f.canopy_area_m2.values[planted])),
        "mean_confidence": float(conf.mean()),
        "stability_ari_mean": ari_mean, "stability_ari_min": ari_min,
        "spearman_score_vs_vigour_index": float(rho_vig),
        "vigour_index": {"weights": WEIGHTS, "bands_z": {nm: (t if np.isfinite(t) else None) for t, nm in VIGOUR_BANDS},
                         "spearman_ndvi_vs_rgb_subscore": float(rho_sub)},
        "field_median": {c: float(np.nanmedian(f[c].astype(float).values[planted]))
                         for c in list(WEIGHTS) + ["bg_ndvi", "mean_ndvi", "canopy_area_m2"]},
        "disclaimer": DISCLAIMER, "processing_date": PROCESSING_DATE,
    }
    write_json(out / "health_summary.json", summary)
    preview(res, names, out / "health_preview.png")
    group_examples(crops[np.flatnonzero(planted)], lab, names, conf, out / "health_groups.png")
    print({k_: v for k_, v in summary.items() if k_ in ("k", "bic_by_k", "class_counts", "stability_ari_mean",
                                                       "spearman_score_vs_vigour_index", "mean_confidence",
                                                       "green_canopy_planted", "no_green_canopy_planted",
                                                       "median_canopy_m2_planted")})
    for p_ in profiles:
        print(f"  {p_['name']:<22} n={p_['n_plants']:<5} {p_['profile']}")
    return summary


def group_colors(n):
    ramp = {3: ["#12805a", "#9a9994", "#c93a3a"],
            4: ["#12805a", "#6cc79f", "#ef9a8a", "#c93a3a"],
            5: ["#12805a", "#6cc79f", "#9a9994", "#ef9a8a", "#c93a3a"]}
    return ramp[n]


def preview(res, names, path, window=(4245, 2448, 1024)):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import rasterio
    from rasterio.windows import Window
    c0, r0, s = window
    with rasterio.open(OUT / "aligned" / "rgb_aligned.tif") as d:
        c0, r0 = min(c0, max(d.width - s, 0)), min(r0, max(d.height - s, 0))
        img = np.moveaxis(d.read([1, 2, 3], window=Window(c0, r0, s, s)), 0, -1)
        x0, y0 = d.transform * (c0, r0)
        x1, y1 = d.transform * (c0 + s, r0 + s)
    fig, ax = plt.subplots(figsize=(10, 10))
    ax.imshow(img, extent=[x0, x1, y1, y0])
    sub = res.cx[x0:x1, y1:y0]
    for name, col in zip(names, group_colors(len(names))):
        part = sub[sub.health_class == name]
        if len(part):
            part.plot(ax=ax, color=col, alpha=0.6, edgecolor="k", linewidth=0.4)
    ax.set_xlim(x0, x1); ax.set_ylim(y1, y0); ax.axis("off")
    ax.set_title("Plant Health (unsupervised condition groups) - green = best, red = poorest")
    plt.tight_layout(); plt.savefig(path, dpi=90); plt.close()


def group_examples(crops, lab, names, conf, path, n=8):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    rng = np.random.default_rng(2)
    fig, ax = plt.subplots(len(names), n, figsize=(2 * n, 2.2 * len(names)))
    for r, name in enumerate(names):
        idx = np.flatnonzero(lab == r)
        pick = rng.choice(idx, size=min(n, len(idx)), replace=False) if len(idx) else []
        for j in range(n):
            a = ax[r, j]
            a.axis("off")
            if j < len(pick):
                a.imshow(crops[pick[j]])
                a.set_title(f"{conf[pick[j]]:.2f}", fontsize=8)
        ax[r, 0].text(-0.1, 0.5, name, transform=ax[r, 0].transAxes, rotation=90, va="center", ha="right", fontsize=9)
    plt.suptitle("Plant Health groups: example 1.2 m crops (6 mm) - title = group probability")
    plt.tight_layout(); plt.savefig(path, dpi=80); plt.close()


if __name__ == "__main__":
    main()
