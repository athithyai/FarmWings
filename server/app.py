"""FarmWings compute server - runs the full processing pipeline for uploaded surveys.

The FarmWings app (GitHub Pages or local) uploads an RGB orthomosaic + NDVI GeoTIFF here; the
server runs exactly the same pipeline as the Pilot (processing/run_pipeline.py: alignment,
drip lines, SAM 2.1 detection, DINOv3-SAT identification, unsupervised health model, web
export) on this machine's GPU and serves the results back to the app.

    .venv\\Scripts\\python.exe server/app.py                 # http://127.0.0.1:8765
    .venv\\Scripts\\python.exe server/app.py --port 9000 --origins https://example.org
    python server/app.py --google-client-id <id>.apps.googleusercontent.com --allow you@example.com,@yourcompany.com

Jobs run one at a time (one GPU). Each job lives in server_jobs/<id>/ (input/, outputs/, web/,
pipeline.log, job.json) and survives restarts.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

ROOT = Path(__file__).resolve().parents[1]
JOBS = Path(os.environ.get("FARMWINGS_JOBS", ROOT / "server_jobs"))
PIPELINE = ROOT / "processing" / "run_pipeline.py"
STAGES = ["inspect", "align", "lines", "detect", "identify", "health", "export", "tiles", "media"]
# rough share of run time per stage (Pilot run on an RTX 5070 laptop GPU) for the progress bar
WEIGHT = {"inspect": 3, "align": 10, "lines": 2, "detect": 55, "identify": 12, "health": 6,
          "export": 1, "tiles": 9, "media": 2}
VERSION = "0.2.0"
DEFAULT_ORIGINS = r"https://athithyai\.github\.io|http://(localhost|127\.0\.0\.1)(:\d+)?"

app = FastAPI(title="FarmWings compute server", version=VERSION)

# ------------------------------------------------------------------ sign-in (Google)
# Enabled when a Google OAuth client ID is configured (--google-client-id or
# FARMWINGS_GOOGLE_CLIENT_ID). The app signs the user in with Google Identity Services and sends
# the Google ID token once; the server verifies it (signature, audience, issuer, expiry,
# verified e-mail), checks the allow-list and returns a session token used for every request.
AUTH = {"client_id": None, "allow": []}
SESSIONS: dict[str, dict] = {}
SESSION_HOURS = 12


def email_allowed(email: str) -> bool:
    email = email.lower()
    for rule in AUTH["allow"]:
        rule = rule.strip().lower()
        if rule == "*" or rule == email or (rule.startswith("@") and email.endswith(rule)):
            return True
    return False


def session_for(token: str | None) -> dict:
    if not AUTH["client_id"]:
        return {"email": None}                       # sign-in disabled (local use)
    sess = SESSIONS.get(token or "")
    if not sess or sess["exp"] < time.time():
        SESSIONS.pop(token or "", None)
        raise HTTPException(401, "sign in required")
    return sess


def require_user(request: Request) -> dict:
    h = request.headers.get("authorization", "")
    return session_for(h[7:] if h.lower().startswith("bearer ") else None)
jobs: dict[str, dict] = {}
work: "queue.Queue[str]" = queue.Queue()
lock = threading.Lock()


# ------------------------------------------------------------------ job bookkeeping
def job_dir(jid: str) -> Path:
    return JOBS / jid


def save(job: dict):
    with lock:
        jobs[job["id"]] = job
        (job_dir(job["id"]) / "job.json").write_text(json.dumps(job, indent=1), encoding="utf-8")


def public(job: dict) -> dict:
    j = {k: v for k, v in job.items() if k != "cmd"}
    j["queue_position"] = list(work.queue).index(job["id"]) + 1 if job["id"] in list(work.queue) else 0
    return j


def load_existing():
    JOBS.mkdir(parents=True, exist_ok=True)
    for f in JOBS.glob("*/job.json"):
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if j.get("status") in ("queued", "running"):
            j["status"], j["error"] = "failed", "interrupted: the server stopped while this job was pending"
        jobs[j["id"]] = j
        (f).write_text(json.dumps(j, indent=1), encoding="utf-8")


def gpu_name() -> str | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        return out or None
    except Exception:
        return None


# ------------------------------------------------------------------ validation
def inspect_geotiff(path: Path, kind: str) -> dict:
    import rasterio
    try:
        with rasterio.open(path) as d:
            info = {"width": d.width, "height": d.height, "count": d.count, "crs": d.crs.to_string() if d.crs else None,
                    "pixel_size_m": abs(d.transform.a), "projected": bool(d.crs and d.crs.is_projected),
                    "dtype": d.dtypes[0]}
    except Exception as e:
        raise HTTPException(400, f"{kind}: not a readable GeoTIFF ({e})")
    if not info["crs"]:
        raise HTTPException(400, f"{kind}: the file has no coordinate reference system")
    if not info["projected"]:
        raise HTTPException(400, f"{kind}: CRS {info['crs']} is geographic; reproject to a metric CRS such as UTM")
    if kind == "RGB" and info["count"] < 3:
        raise HTTPException(400, "RGB: needs at least 3 bands (red, green, blue)")
    if kind == "NDVI" and info["count"] != 1:
        raise HTTPException(400, "NDVI: needs exactly 1 band")
    return info


# ------------------------------------------------------------------ worker
def worker():
    while True:
        jid = work.get()
        job = jobs.get(jid)
        if not job or job.get("status") != "queued":
            continue
        d = job_dir(jid)
        cmd = [sys.executable, "-u", str(PIPELINE), "--input", str(d / "input"), "--out", str(d / "outputs"),
               "--web", str(d / "web"), "--project", job["name"], "--species", job["species"]]
        skip = [o for o in ("identify", "health") if o not in job.get("operations", ["identify", "health"])]
        if skip:
            cmd += ["--skip", ",".join(skip)]
        job.update(status="running", started=time.time(), stage="inspect", progress=0.0)
        save(job)
        done_w = 0.0
        total_w = sum(WEIGHT.values())
        with open(d / "pipeline.log", "w", encoding="utf-8") as log:
            proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                    encoding="utf-8", errors="replace", env={**os.environ, "PYTHONUNBUFFERED": "1"})
            job["pid"] = proc.pid
            for line in proc.stdout:
                log.write(line)
                log.flush()
                m = re.match(r"=== (\w+) ===", line)
                if m and m.group(1) in STAGES:
                    job["stage"] = m.group(1)
                    save(job)
                m = re.match(r"=== (\w+) done in (\d+) s", line)
                if m and m.group(1) in STAGES:
                    done_w += WEIGHT[m.group(1)]
                    job["progress"] = round(done_w / total_w, 3)
                    job.setdefault("stage_seconds", {})[m.group(1)] = int(m.group(2))
                    save(job)
                if jobs.get(jid, {}).get("status") == "cancelled":
                    proc.kill()
            rc = proc.wait()
        if job.get("status") == "cancelled":
            save(job)
            continue
        job["finished"] = time.time()
        if rc == 0 and (d / "web" / "summary.json").exists():
            s = json.loads((d / "web" / "summary.json").read_text(encoding="utf-8"))
            job.update(status="done", progress=1.0, stage="done", stats=s.get("stats"))
        else:
            tail = (d / "pipeline.log").read_text(encoding="utf-8", errors="replace").strip().splitlines()[-12:]
            job.update(status="failed", error="\n".join(tail))
        save(job)


# ------------------------------------------------------------------ API
@app.get("/", response_class=HTMLResponse)
def index():
    return ("<h1>FarmWings compute server</h1><p>Running. Open the FarmWings app and go to "
            "<b>Analyze</b> to upload an RGB + NDVI survey.</p><p><a href='/api/status'>/api/status</a></p>")


@app.get("/api/status")
def status():
    return {"ok": True, "service": "farmwings-compute", "version": VERSION, "gpu": GPU,
            "queued": work.qsize(), "running": sum(j.get("status") == "running" for j in jobs.values()),
            "jobs": len(jobs),
            "auth": {"required": bool(AUTH["client_id"]), "provider": "google", "client_id": AUTH["client_id"]}}


@app.post("/api/auth/google")
async def auth_google(request: Request):
    if not AUTH["client_id"]:
        raise HTTPException(400, "sign-in is not enabled on this compute node")
    from google.auth.transport import requests as grequests
    from google.oauth2 import id_token
    body = await request.json()
    try:
        info = id_token.verify_oauth2_token(body.get("credential", ""), grequests.Request(), AUTH["client_id"])
    except Exception as e:
        raise HTTPException(401, f"Google sign-in could not be verified ({e})")
    if info.get("iss") not in ("accounts.google.com", "https://accounts.google.com") or not info.get("email_verified"):
        raise HTTPException(401, "Google account e-mail is not verified")
    email = info["email"]
    if not email_allowed(email):
        raise HTTPException(403, f"{email} is not on this compute node's access list")
    token = secrets.token_urlsafe(24)
    SESSIONS[token] = {"email": email, "name": info.get("name"), "picture": info.get("picture"),
                       "exp": time.time() + SESSION_HOURS * 3600}
    return {"token": token, **{k: v for k, v in SESSIONS[token].items()}}


@app.get("/api/auth/me")
def auth_me(user: dict = Depends(require_user)):
    return user


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    h = request.headers.get("authorization", "")
    SESSIONS.pop(h[7:] if h.lower().startswith("bearer ") else "", None)
    return {"ok": True}


@app.get("/api/jobs")
def list_jobs(user: dict = Depends(require_user)):
    return sorted((public(j) for j in jobs.values()), key=lambda j: -j["created"])


@app.post("/api/jobs")
async def create_job(rgb: UploadFile = File(...), ndvi: UploadFile = File(...),
                     name: str = Form("Uploaded survey"), species: str = Form("Rhanterium epapposum"),
                     operations: str = Form("detect,identify,health"), user: dict = Depends(require_user)):
    jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    d = job_dir(jid)
    (d / "input").mkdir(parents=True)
    try:
        for up, fn in [(rgb, "rgb.tif"), (ndvi, "ndvi.tif")]:
            with open(d / "input" / fn, "wb") as f:
                while chunk := await up.read(8 << 20):
                    f.write(chunk)
        info = {"rgb": inspect_geotiff(d / "input" / "rgb.tif", "RGB"),
                "ndvi": inspect_geotiff(d / "input" / "ndvi.tif", "NDVI")}
        if info["rgb"]["crs"] != info["ndvi"]["crs"]:
            raise HTTPException(400, f"RGB CRS {info['rgb']['crs']} differs from NDVI CRS {info['ndvi']['crs']}; "
                                     "reproject one of them first")
    except HTTPException:
        shutil.rmtree(d, ignore_errors=True)
        raise
    ops = [o for o in ("detect", "identify", "health") if o in operations.split(",") or o == "detect"]
    job = {"id": jid, "name": name.strip()[:80] or "Uploaded survey", "species": species.strip()[:60] or "Planted sapling",
           "operations": ops,
           "created": time.time(), "status": "queued", "stage": None, "progress": 0.0,
           "files": {"rgb": rgb.filename, "ndvi": ndvi.filename}, "inputs": info, "created_by": user.get("email")}
    save(job)
    work.put(jid)
    return public(job)


@app.get("/api/jobs/{jid}")
def get_job(jid: str, user: dict = Depends(require_user)):
    job = jobs.get(jid)
    if not job:
        raise HTTPException(404, "no such job")
    j = public(job)
    log = job_dir(jid) / "pipeline.log"
    j["log_tail"] = log.read_text(encoding="utf-8", errors="replace").splitlines()[-15:] if log.exists() else []
    return j


@app.delete("/api/jobs/{jid}")
def delete_job(jid: str, user: dict = Depends(require_user)):
    job = jobs.get(jid)
    if not job:
        raise HTTPException(404, "no such job")
    if job.get("status") == "running":
        job["status"] = "cancelled"
        save(job)
        return {"cancelled": jid}
    shutil.rmtree(job_dir(jid), ignore_errors=True)
    jobs.pop(jid, None)
    return {"deleted": jid}


@app.get("/api/jobs/{jid}/data/{path:path}")
def job_data(jid: str, path: str, user: dict = Depends(require_user)):
    return _serve(jid, path)


@app.get("/api/s/{token}/jobs/{jid}/data/{path:path}")
def job_data_session(token: str, jid: str, path: str):
    """Same as above with the session in the path, so images and map tiles (which cannot send
    headers) load for signed-in users."""
    session_for(token)
    return _serve(jid, path)


def _serve(jid: str, path: str):
    base = (job_dir(jid) / "web").resolve()
    p = (base / path).resolve()
    if base not in p.parents or not p.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(p)


class PrivateNetworkAccess:
    """Chrome asks public HTTPS pages for permission before they call localhost (Private /
    Local Network Access) and expects this header on the preflight response."""

    def __init__(self, app_):
        self.app = app_

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append((b"access-control-allow-private-network", b"true"))
            await send(message)
        return await self.app(scope, receive, send_wrapper)


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


GPU = None


def main():
    global GPU
    ap = argparse.ArgumentParser(description="FarmWings compute server")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--origins", default=DEFAULT_ORIGINS, help="regex of allowed app origins")
    ap.add_argument("--google-client-id", default=os.environ.get("FARMWINGS_GOOGLE_CLIENT_ID"),
                    help="Google OAuth client ID; enables 'Sign in with Google' for this node")
    ap.add_argument("--allow", default=os.environ.get("FARMWINGS_ALLOW", ""),
                    help="comma-separated e-mails and/or @domains allowed to sign in ('*' = any Google account)")
    a = ap.parse_args()
    AUTH["client_id"] = a.google_client_id or None
    AUTH["allow"] = [x for x in a.allow.split(",") if x.strip()]
    if AUTH["client_id"] and not AUTH["allow"]:
        raise SystemExit("--allow is required with --google-client-id (who may sign in?)")
    GPU = gpu_name()
    load_existing()
    app.add_middleware(CORSMiddleware, allow_origin_regex=a.origins, allow_methods=["*"], allow_headers=["*"])
    app.add_middleware(PrivateNetworkAccess)
    threading.Thread(target=worker, daemon=True).start()
    print(f"FarmWings compute server {VERSION} on http://{a.host}:{a.port}  GPU: {GPU or 'none detected'}  "
          f"sign-in: {'Google, ' + str(len(AUTH['allow'])) + ' allow rules' if AUTH['client_id'] else 'off (local use)'}")
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
