// Who is using the app. Two ways in: Sign in with Google (once a client ID is set in
// public/config.json) or demo access. The web app is static and its data is public, so this
// session only decides which screens open; the compute node verifies Google tokens itself.

const KEY = "farmwings.user";
const DAYS = 7;
let configPromise = null;
let memory = null;   // fallback when localStorage is blocked

export function siteConfig() {
  configPromise ??= fetch(new URL("config.json", document.baseURI))
    .then((r) => (r.ok ? r.json() : {}))
    .catch(() => ({}));
  return configPromise;
}

export function currentUser() {
  try {
    const u = JSON.parse(localStorage.getItem(KEY) || "null");
    return u && u.exp > Date.now() ? u : null;
  } catch {
    return null;
  }
}

function save(u) {
  const user = { ...u, exp: Date.now() + DAYS * 864e5 };
  try { localStorage.setItem(KEY, JSON.stringify(user)); } catch { /* storage blocked: session lasts this page only */ }
  memory = user;
  return user;
}
export function user() { return currentUser() || memory; }

export function signInDemo() {
  return save({ kind: "demo", name: "Demo access", email: null, picture: null });
}

// Google Identity Services returns a signed ID token (JWT); its payload carries the profile.
export function signInGoogle(credential) {
  const part = credential.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
  const p = JSON.parse(decodeURIComponent(escape(atob(part.padEnd(part.length + ((4 - (part.length % 4)) % 4), "=")))));
  return save({ kind: "google", name: p.name || p.email, email: p.email, picture: p.picture || null });
}

export function signOut() {
  memory = null;
  try { localStorage.removeItem(KEY); } catch { /* ignore */ }
  try { window.google?.accounts?.id?.disableAutoSelect(); } catch { /* ignore */ }
}

export function loadGoogle() {
  if (window.google?.accounts?.id) return Promise.resolve();
  return new Promise((res, rej) => {
    const sc = document.createElement("script");
    sc.src = "https://accounts.google.com/gsi/client";
    sc.async = true;
    sc.onload = res;
    sc.onerror = () => rej(new Error("Google sign-in could not load"));
    document.head.appendChild(sc);
  });
}

export function initials(u) {
  const n = (u?.name || "").trim();
  if (!n || u.kind === "demo") return "D";
  return n.split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
}
