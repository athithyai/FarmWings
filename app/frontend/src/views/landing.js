// Public landing page: what FarmWings does, shown on one real patch of the Pilot field.
import { esc, fmt } from "../ui.js";
import { detectionFlow, flowLegend, healthFlow, identificationFlow } from "../diagrams.js";
import { user } from "../session.js";

const img = (n) => new URL(`showcase/${n}.webp`, document.baseURI).href;

const HEALTH = [["Very good", "#12805a"], ["Good", "#6cc79f"], ["Fair", "#9a9994"], ["Poor", "#ef9a8a"], ["Very poor", "#c93a3a"]];
const STEPS = [
  {
    key: "fly", title: "Fly once: colour + NDVI", images: ["rgb", "ndvi"],
    text: "One drone flight gives two layers. The 6 mm colour orthomosaic shows every sapling, planting pit and drip line. The NDVI map shows where there is living green canopy. FarmWings aligns the two to within a few centimetres.",
    legend: [["Drone colour image", null], ["NDVI: low → high greenness", "linear-gradient(90deg,#d7301f,#fdae61,#ffffbf,#a6d96a,#1a9850)"]],
  },
  {
    key: "detect", title: "Find every plant", images: ["detect"],
    text: "FarmWings finds the drip lines and outlines every plant with SAM 2.1. Each planting spot along a line gets exactly one plant. A spot where a plant should be but none grows is flagged as empty. Plants between the lines are counted separately.",
    legend: [["Planted sapling", "#ffd166", "ring"], ["Between-line plant", "#5fd4e8", "ring"], ["Empty planting spot", "#ff6f91", "ring"], ["Drip line", "#ffffff", "line"]],
  },
  {
    key: "identify", title: "Tell planted stock from weeds", images: ["identify"],
    text: "DINOv3, a vision model pretrained on 493 million satellite images, describes each plant crop. A classifier trained on the planting layout uses that description, with NDVI, colour and shape, to separate the planted species from other vegetation.",
    legend: [["Planted species", "#3987e5"], ["Other vegetation", "#d95926"], ["Unclassified", "#898781"]],
  },
  {
    key: "health", title: "Check every sapling's condition", images: ["health"],
    text: "Saplings are grouped by how they look and how green they are, with no hand labels needed. A Gaussian mixture model groups the DINOv3 descriptions plus six NDVI and colour indicators. The groups are ranked from very good to very poor, so the field team knows where to look first.",
    legend: HEALTH.map(([k, c]) => [k, c]),
  },
];

function legendHtml(items) {
  return items.map(([k, c, kind]) => `<span class="lg">${c ? `<i class="${kind || ""}" style="${kind === "ring" ? `border-color:${c}` : kind === "line" ? `background:${c}` : `background:${c}`}"></i>` : ""}${esc(k)}</span>`).join("");
}

export function renderLanding(el) {
  const u = user();
  const cta = u
    ? `<a class="btn primary lg" href="#/projects">Open your projects</a>`
    : `<a class="btn primary lg" href="#/signin">Sign in</a><a class="btn lg" href="#/signin?demo=1">Try the demo</a>`;

  el.innerHTML = `
  <section class="l-hero">
    <div class="wrap l-hero-grid">
      <div>
        <div class="eyebrow">Drone mapping &amp; plant intelligence</div>
        <h1>Every sapling, counted and checked from the air.</h1>
        <p class="lede">FarmWings turns one drone survey into a plant-by-plant record: where each plant is, what it is and how it is doing. Your team opens it on a map, anywhere.</p>
        <div class="l-cta">${cta}</div>
        <ul class="l-points">
          <li><b>Plant level</b><span>One record per sapling, not field averages</span></li>
          <li><b>RGB + NDVI</b><span>From a standard multispectral flight</span></li>
          <li><b>~30 min</b><span>GPU time for a 2.7 ha block</span></li>
        </ul>
      </div>
      <div class="compare" style="--pos:52%">
        <img class="c-base" src="${img("rgb")}" alt="Drone image of saplings along drip lines" />
        <img class="c-top" src="${img("health")}" alt="The same saplings coloured by FarmWings condition group" />
        <div class="c-handle" aria-hidden="true"><span>⟷</span></div>
        <span class="c-lab l">Drone image</span><span class="c-lab r">FarmWings result</span>
        <input type="range" min="0" max="100" value="52" aria-label="Slide to compare the drone image with the FarmWings result" />
      </div>
    </div>
  </section>

  <section class="l-sec" id="how">
    <div class="wrap">
      <div class="l-head"><div class="eyebrow">How it works</div><h2>From flight to plant record</h2>
        <p>One 9 × 6 m patch of the Pilot field, shown at every step.</p></div>
      <div class="stepper">
        <div class="step-tabs" role="tablist" aria-label="Steps">
          ${STEPS.map((s, i) => `<button role="tab" class="step-tab" data-i="${i}" aria-selected="${i === 0}"><span class="n num">0${i + 1}</span>${esc(s.title)}</button>`).join("")}
        </div>
        <div class="step-body">
          <div class="step-img">
            ${["rgb", "ndvi", "detect", "identify", "health"].map((n) => `<img data-img="${n}" src="${img(n)}" alt="" loading="lazy" />`).join("")}
            <div class="img-toggle" hidden><button data-show="rgb" aria-pressed="true">Colour</button><button data-show="ndvi" aria-pressed="false">NDVI</button></div>
            <span class="scale"><i></i>1 m</span>
          </div>
          <div class="step-text">
            <div class="n num" id="st-n"></div>
            <h3 id="st-title"></h3>
            <p id="st-text"></p>
            <div class="legend-inline" id="st-legend"></div>
            <div class="step-nav"><button class="btn" id="st-prev">Back</button><button class="btn primary" id="st-next">Next step</button></div>
          </div>
        </div>
      </div>
    </div>
  </section>

  <section class="l-sec alt" id="models">
    <div class="wrap">
      <div class="l-head"><div class="eyebrow">The models</div><h2>Three models, one plant record</h2>
        <p>Each result keeps the model that produced it. Open models, run on your own GPU or a cloud GPU.</p></div>
      <div class="model-block">
        <div class="mb-head"><span class="num">1</span><div><h3>Detection: where is each plant?</h3>
          <p>SAM 2.1 outlines the plants; the drip lines and the planting rhythm decide which outline is the sapling in each planting spot.</p></div></div>
        ${detectionFlow()}
      </div>
      <div class="model-block">
        <div class="mb-head"><span class="num">2</span><div><h3>Identification: what is it?</h3>
          <p>A satellite-pretrained vision model describes each plant; a small classifier separates the planted species from other vegetation.</p></div></div>
        ${identificationFlow()}
      </div>
      <div class="model-block">
        <div class="mb-head"><span class="num">3</span><div><h3>Health: how is it doing?</h3>
          <p>An unsupervised model groups the saplings by appearance and greenness; no field labels are needed. With 100–300 field-scored plants, the same features train a supervised health model.</p></div></div>
        ${healthFlow()}
      </div>
      ${flowLegend}
    </div>
  </section>

  <section class="l-sec" id="product">
    <div class="wrap">
      <div class="l-head"><div class="eyebrow">What your team gets</div><h2>A workspace for every project</h2>
        <p>Pick the project on the map; its results open in one place.</p></div>
      <div class="shots">
        ${[
          ["screen_overview", "Plant count at a glance", "Planting spots, plants found, green canopy, identification and health on one page."],
          ["screen_map", "Every plant on the map", "Colour by detection, canopy, identity or health; draw an area to get its numbers."],
          ["screen_plants", "Plant-by-plant inventory", "Filter, sort and export; every plant has its own page with its crops and measurements."],
          ["screen_insights", "Where to act", "Drip lines and plants that need a field visit first."],
        ].map(([f, t, d]) => `<figure class="shot"><div class="shot-img"><img src="${img(f)}" alt="${esc(t)} screen" loading="lazy" /></div>
          <figcaption><b>${esc(t)}</b><span>${esc(d)}</span></figcaption></figure>`).join("")}
      </div>
    </div>
  </section>

  <section class="l-sec alt" id="results">
    <div class="wrap">
      <div class="l-head"><div class="eyebrow">Proven on a pilot</div><h2>Checked against the installation record</h2>
        <p id="val-note">A 2.7 ha revegetation block with drip-irrigated saplings.</p></div>
      <div class="proof" id="proof"></div>
    </div>
  </section>

  <section class="l-final">
    <div class="wrap">
      <h2>See it on a real project</h2>
      <p>Sign in, pick the Pilot on the map, and explore every sapling.</p>
      <div class="l-cta center">${cta}</div>
    </div>
  </section>

  <footer class="l-foot">
    <div class="wrap">
      <span class="brand-mini">Farm<b>Wings</b> <small>from SpatialWings</small></span>
      <span class="muted small">Health groups are inferred from RGB and NDVI; they are not a laboratory disease diagnosis.</span>
      <a class="small" href="https://github.com/athithyai/FarmWings" target="_blank" rel="noopener">GitHub</a>
    </div>
  </footer>`;

  // before / after slider
  const cmp = el.querySelector(".compare");
  cmp.querySelector("input").addEventListener("input", (e) => cmp.style.setProperty("--pos", `${e.target.value}%`));

  // step-through
  let cur = 0;
  let shown = "rgb";
  const imgs = el.querySelectorAll(".step-img img");
  const toggle = el.querySelector(".img-toggle");
  function show(name) {
    shown = name;
    imgs.forEach((im) => im.classList.toggle("on", im.dataset.img === name));
    toggle.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.show === name)));
  }
  function go(i) {
    cur = (i + STEPS.length) % STEPS.length;
    const s = STEPS[cur];
    el.querySelectorAll(".step-tab").forEach((t) => t.setAttribute("aria-selected", String(+t.dataset.i === cur)));
    el.querySelector("#st-n").textContent = `Step 0${cur + 1} of 0${STEPS.length}`;
    el.querySelector("#st-title").textContent = s.title;
    el.querySelector("#st-text").textContent = s.text;
    el.querySelector("#st-legend").innerHTML = legendHtml(s.legend);
    toggle.hidden = s.images.length < 2;
    show(s.images.includes(shown) ? shown : s.images[0]);
    el.querySelector("#st-next").textContent = cur === STEPS.length - 1 ? "Start again" : "Next step";
    el.querySelector("#st-prev").disabled = cur === 0;
  }
  el.querySelectorAll(".step-tab").forEach((t) => t.addEventListener("click", () => go(+t.dataset.i)));
  el.querySelector(".step-tabs").addEventListener("keydown", (e) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    go(cur + (e.key === "ArrowRight" ? 1 : -1));
    el.querySelector(`.step-tab[data-i="${cur}"]`).focus();
  });
  toggle.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => show(b.dataset.show)));
  el.querySelector("#st-next").addEventListener("click", () => go(cur + 1));
  el.querySelector("#st-prev").addEventListener("click", () => go(cur - 1));
  go(0);

  // pilot proof numbers (summary only, 12 kB)
  fetch(new URL("data/summary.json", document.baseURI)).then((r) => r.json()).then((s) => {
    const v = s.validation;
    const st = s.stats;
    el.querySelector("#val-note").textContent = `A ${fmt.n(st.surveyed_area_ha, 1)} ha revegetation block, ${fmt.int(st.expected_planting_positions)} planting spots on ${st.planting_lines} drip lines. The project's own installation record (${fmt.int(v.reference_points)} points) was used as a reference, not as ground truth.`;
    el.querySelector("#proof").innerHTML = [
      [fmt.pct(v.precision, 1), "of FarmWings plants sit on a recorded planting point"],
      [fmt.pct(v.recall, 1), "of recorded points have a FarmWings plant"],
      [`${fmt.n(v.median_distance_m * 100, 1)} cm`, "median distance between the two"],
      [fmt.int(st.no_green_canopy_planted + st.inferred_missing_positions), "spots flagged for a field check: no green canopy or empty"],
    ].map(([v1, d]) => `<div class="proof-item"><div class="v">${v1}</div><div class="d">${esc(d)}</div></div>`).join("");
  }).catch(() => { el.querySelector("#results").hidden = true; });
}
