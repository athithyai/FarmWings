// Sign-in screen: Google (when a client ID is configured) or clearly labelled demo access.
import { esc } from "../ui.js";
import { loadGoogle, signInDemo, signInGoogle, siteConfig } from "../session.js";

const img = (n) => new URL(`showcase/${n}.webp`, document.baseURI).href;

export function renderSignin(el, params, done) {
  const next = params.get("next") || "#/projects";
  if (params.get("demo") === "1") {   // "Try the demo" links straight in
    signInDemo();
    done(next);
    return;
  }
  el.innerHTML = `
  <div class="auth">
    <div class="auth-visual">
      <img src="${img("detect")}" alt="" />
      <div class="auth-quote">
        <b>Every sapling, counted and checked from the air.</b>
        <span>Detection, identification and health for each plant, from one drone flight.</span>
      </div>
    </div>
    <div class="auth-panel">
      <div class="auth-card">
        <div class="auth-brand"><svg class="logo" viewBox="0 0 32 32" aria-hidden="true"><path d="M16 27c0-8 3-13 10-16-7 0-10 3-10 8 0-5-3-8-10-8 7 3 10 8 10 16z" /></svg>
          <span><span class="brand-name">Farm<b>Wings</b></span><span class="brand-from">from SpatialWings</span></span></div>
        <h1>Sign in</h1>
        <p class="muted">Open your projects and their plant-level results.</p>
        <div id="gsi" class="gsi"><button class="btn google" disabled>${googleIcon} Sign in with Google</button></div>
        <p class="small muted" id="gsi-note"></p>
        <div class="or"><span>or</span></div>
        <button class="btn demo" id="demo"><b>Continue with demo access</b><span>Explore the Pilot project with its processed results</span></button>
        <p class="small muted demo-note"><span class="badge">Demo</span> No account needed. Demo access is remembered in this browser only; uploading new surveys needs a connected compute node.</p>
        <a class="small" href="#/">← Back to FarmWings</a>
      </div>
    </div>
  </div>`;

  el.querySelector("#demo").addEventListener("click", () => { signInDemo(); done(next); });

  siteConfig().then(async (cfg) => {
    const note = el.querySelector("#gsi-note");
    if (!cfg.google_client_id) {
      note.textContent = "Google sign-in switches on once an OAuth client ID is added to config.json.";
      return;
    }
    try {
      await loadGoogle();
      window.google.accounts.id.initialize({
        client_id: cfg.google_client_id,
        callback: ({ credential }) => { signInGoogle(credential); done(next); },
      });
      const box = el.querySelector("#gsi");
      box.innerHTML = "";
      window.google.accounts.id.renderButton(box, { theme: "outline", size: "large", text: "signin_with", shape: "pill", width: 320 });
    } catch (e) {
      note.textContent = `${esc(e.message)}. Use demo access for now.`;
    }
  });
}

const googleIcon = `<svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"/><path fill="#FF3D00" d="m6.3 14.7 6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/><path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z"/><path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z"/></svg>`;
