"""HTTP API + static UI."""

from __future__ import annotations

import json
import mimetypes
import os
import queue
import threading
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import Settings, get_settings
from .describer import Describer
from .service import FileService
from .storage import B2Storage, StorageError

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="B2 Describe",
    description="Store a file in Backblaze B2, caption/summarize it with Claude, attach the description as object metadata.",
    version="1.0.0",
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@lru_cache
def _service() -> FileService:
    s = get_settings()
    return FileService(B2Storage(s), Describer(s))


def get_service() -> FileService:
    missing = get_settings().b2_missing()
    if missing:
        raise HTTPException(503, f"Backblaze B2 is not configured. Missing in .env: {', '.join(missing)}. See docs/B2_SETUP.md.")
    return _service()


def _respond(result) -> JSONResponse:
    code = 201 if result.status != "failed" else 502
    return JSONResponse(result.to_dict(), status_code=code)


def _run(stream: bool, job):
    """Run `job(on_stage) -> Result`. Plain JSON by default; with ?stream=true,
    NDJSON stage events then the result.

    Stream lines:  {"event":"stage","stage":"stored"} ... {"event":"result","result":{...}}
    """
    if not stream:
        return _respond(job(None))

    events: queue.Queue = queue.Queue()

    def work():
        try:
            r = job(lambda s: events.put({"event": "stage", "stage": s}))
            events.put({"event": "result", "result": r.to_dict()})
        except Exception as e:  # never leave the client hanging
            events.put({"event": "error", "error": f"Unexpected error: {e}"})
        events.put(None)

    threading.Thread(target=work, daemon=True).start()

    def lines():
        while (ev := events.get()) is not None:
            yield json.dumps(ev) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# --- routes -----------------------------------------------------------------

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health(settings: Settings = Depends(get_settings)):
    missing = settings.b2_missing()
    return {
        "b2": {"configured": not missing, "missing": missing, "bucket": settings.b2_bucket or None},
        "claude": {"configured": bool(settings.resolve_anthropic_key()), "model": settings.claude_model},
        "max_upload_mb": settings.max_upload_mb,
        "local_path_root": os.path.expanduser(settings.local_path_root),
    }


@app.post("/api/files", status_code=201, summary="Upload a file (multipart/form-data, field 'file')")
def upload_file(
    file: UploadFile = File(...),
    stream: bool = Query(False, description="Stream NDJSON progress events"),
    service: FileService = Depends(get_service),
    settings: Settings = Depends(get_settings),
):
    limit = settings.max_upload_bytes
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"File exceeds the {settings.max_upload_mb} MB limit.")
    if not data:
        raise HTTPException(400, "File is empty.")
    name = file.filename or "upload"
    return _run(stream, lambda cb: service.process(data, name, file.content_type, "upload", cb))


class PathRequest(BaseModel):
    path: str


@app.post("/api/files/from-path", status_code=201, summary="Store a file from a local path on the server")
def upload_from_path(
    body: PathRequest,
    stream: bool = Query(False, description="Stream NDJSON progress events"),
    service: FileService = Depends(get_service),
    settings: Settings = Depends(get_settings),
):
    root = Path(os.path.expanduser(settings.local_path_root)).resolve()
    p = Path(os.path.expanduser(body.path.strip())).resolve()
    if not p.is_relative_to(root):
        raise HTTPException(403, f"Path must be inside {root} (LOCAL_PATH_ROOT).")
    if not p.is_file():
        raise HTTPException(404, "File not found.")
    if p.stat().st_size > settings.max_upload_bytes:
        raise HTTPException(413, f"File exceeds the {settings.max_upload_mb} MB limit.")
    if p.stat().st_size == 0:
        raise HTTPException(400, "File is empty.")
    ctype = mimetypes.guess_type(p.name)[0]
    data = p.read_bytes()
    return _run(stream, lambda cb: service.process(data, p.name, ctype, "local-path", cb))


class KeyRequest(BaseModel):
    key: str


@app.post("/api/files/redescribe", summary="Retry the AI description of an already-stored file (no re-upload)")
def redescribe(
    body: KeyRequest,
    stream: bool = Query(False, description="Stream NDJSON progress events"),
    service: FileService = Depends(get_service),
    settings: Settings = Depends(get_settings),
):
    if not body.key.startswith(settings.b2_prefix) or ".." in body.key:
        raise HTTPException(400, "Key is outside this service's prefix.")
    try:
        obj = service.storage.head(body.key)
    except StorageError:
        raise HTTPException(404, "No such file in the bucket.")
    if obj.size > settings.max_upload_bytes:
        raise HTTPException(413, f"File exceeds the {settings.max_upload_mb} MB limit.")
    return _run(stream, lambda cb: service.redescribe(body.key, cb))


@app.get("/api/files", summary="Recently stored files with their AI metadata")
def list_files(limit: int = Query(20, ge=1, le=100), service: FileService = Depends(get_service)):
    try:
        objs = service.storage.list_recent(limit)
    except StorageError as e:
        raise HTTPException(502, str(e))
    return [
        {
            "key": o.key,
            "filename": o.metadata.get("original-filename", o.key.rsplit("/", 1)[-1]),
            "size": o.size,
            "content_type": o.content_type,
            "uploaded_at": o.metadata.get("uploaded-at"),
            "description": o.metadata.get("ai-description"),
            "description_status": o.metadata.get("ai-status"),
            "tags": [t for t in o.metadata.get("ai-tags", "").split(", ") if t],
            "url": service.storage.presigned_url(o.key),
        }
        for o in objs
    ]
