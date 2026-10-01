// Public landing page: what FarmWings does, shown on one real patch of the Pilot field.
import { esc, fmt } from "../ui.js";
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
    text: "FarmWings AI reads the planting layout and outlines every plant to the centimetre. Each planting spot gets exactly one plant; a spot where a plant should be but none grows is flagged as empty. Weeds between the lines are counted separately.",
    legend: [["Planted sapling", "#ffd166", "ring"], ["Between-line plant", "#5fd4e8", "ring"], ["Drip line", "#ffffff", "line"]],
  },
  {
    key: "identify", title: "Tell planted stock from weeds", images: ["identify"],
    text: "Every plant is recognised by how it looks, how green it is and its shape: planted stock or a weed. Your planting record gives the species; FarmWings confirms what actually grew.",
    legend: [["Planted stock", "#3987e5"], ["Other vegetation", "#d95926"], ["Unclassified", "#898781"]],
  },
  {
    key: "health", title: "Check every sapling's condition", images: ["health"],
    text: "Each sapling gets a condition grade, from very good to very poor, from its greenness, canopy and appearance. No field sampling needed: the team knows where to look first.",
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
      <div class="l-head"><div class="eyebrow">FarmWings AI</div><h2>Three answers for every plant</h2>
        <p>Built in-house for young plantations seen from the air.</p></div>
      <div class="caps">
        <div class="cap-card"><span class="cap-n num">01</span><h3>Where is it?</h3>
          <p>Every sapling located and outlined to the centimetre. Empty planting spots flagged automatically.</p>
          <span class="cap-stat"><b class="num">99.5%</b> agreement with the installation record</span></div>
        <div class="cap-card"><span class="cap-n num">02</span><h3>What is it?</h3>
          <p>Planted stock told apart from weeds and volunteer growth, plant by plant.</p>
          <span class="cap-stat"><b class="num">8,000+</b> plants classified in one survey</span></div>
        <div class="cap-card"><span class="cap-n num">03</span><h3>How is it doing?</h3>
          <p>A condition grade and the green canopy of every sapling, so field visits go where they matter.</p>
          <span class="cap-stat"><b class="num">5</b> condition grades, from very good to very poor</span></div>
      </div>
    </div>
  </section>

  <section class="l-sec" id="product">
    <div class="wrap">
      <div class="l-head"><div class="eyebrow">What your team gets</div><h2>A workspace for every project</h2>
        <p>Pick the project on the map; its results open in one place.</p></div>
      <div class="shots">
        ${[
          ["screen_overview", "Your field, as a story", "Every planting spot, what was found, what is green and where to go first."],
          ["screen_map", "Every plant on the map", "Click a condition to light up every matching plant; draw an area to get its numbers."],
          ["screen_plants", "Plant-by-plant inventory", "Filter, sort and export; every plant has its own page with its crops and measurements."],
          ["screen_insights", "Where to act", "Every chart connected: pick a group, see where it sits, line by line."],
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
      <span class="small foot-links muted">Built with DINOv3 · <a href="${new URL("third-party-licenses.txt", document.baseURI).href}" target="_blank" rel="noopener">Credits</a> · © 2026 FarmWings · All rights reserved</span>
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
    el.querySelector("#val-note").textContent = `A ${fmt.n(st.surveyed_area_ha, 1)} ha revegetation block, ${fmt.int(st.expected_planting_positions)} planting spots on ${st.planting_lines} planting lines. The project's own installation record (${fmt.int(v.reference_points)} points) was used as a reference, not as ground truth.`;
    el.querySelector("#proof").innerHTML = [
      [fmt.pct(v.precision, 1), "of FarmWings plants sit on a recorded planting point"],
      [fmt.pct(v.recall, 1), "of recorded points have a FarmWings plant"],
      [`${fmt.n(v.median_distance_m * 100, 1)} cm`, "median distance between the two"],
      [fmt.int(st.no_green_canopy_planted + st.inferred_missing_positions), "spots flagged for a field check: no green canopy or empty"],
    ].map(([v1, d]) => `<div class="proof-item"><div class="v">${v1}</div><div class="d">${esc(d)}</div></div>`).join("");
  }).catch(() => { el.querySelector("#results").hidden = true; });
}
