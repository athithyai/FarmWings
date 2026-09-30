"""FarmWings compute server - runs the full processing pipeline for uploaded surveys.

The FarmWings app (GitHub Pages or local) uploads an RGB orthomosaic + NDVI GeoTIFF here; the
server runs exactly the same pipeline as the Pilot (processing/run_pipeline.py: alignment,
drip lines, SAM 2.1 detection, DINOv3-SAT identification, unsupervised health model, web
export) on this machine's GPU and serves the results back to the app.

    .venv\\Scripts\\python.exe server/app.py                 # http://127.0.0.1:8765
    .venv\\Scripts\\python.exe server/app.py --port 9000 --origins https://example.org

Jobs run one at a time (one GPU). Each job lives in server_jobs/<id>/ (input/, outputs/, web/,
pipeline.log, job.json) and survives restarts.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
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
            "jobs": len(jobs)}


@app.get("/api/jobs")
def list_jobs():
    return sorted((public(j) for j in jobs.values()), key=lambda j: -j["created"])


@app.post("/api/jobs")
async def create_job(rgb: UploadFile = File(...), ndvi: UploadFile = File(...),
                     name: str = Form("Uploaded survey"), species: str = Form("Palm")):
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
    job = {"id": jid, "name": name.strip()[:80] or "Uploaded survey", "species": species.strip()[:40] or "Palm",
           "created": time.time(), "status": "queued", "stage": None, "progress": 0.0,
           "files": {"rgb": rgb.filename, "ndvi": ndvi.filename}, "inputs": info}
    save(job)
    work.put(jid)
    return public(job)


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    job = jobs.get(jid)
    if not job:
        raise HTTPException(404, "no such job")
    j = public(job)
    log = job_dir(jid) / "pipeline.log"
    j["log_tail"] = log.read_text(encoding="utf-8", errors="replace").splitlines()[-15:] if log.exists() else []
    return j


@app.delete("/api/jobs/{jid}")
def delete_job(jid: str):
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
def job_data(jid: str, path: str):
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
    a = ap.parse_args()
    GPU = gpu_name()
    load_existing()
    app.add_middleware(CORSMiddleware, allow_origin_regex=a.origins, allow_methods=["*"], allow_headers=["*"])
    app.add_middleware(PrivateNetworkAccess)
    threading.Thread(target=worker, daemon=True).start()
    print(f"FarmWings compute server {VERSION} on http://{a.host}:{a.port}  GPU: {GPU or 'none detected'}")
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
