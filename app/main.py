"""HTTP API + static UI."""

from __future__ import annotations

import mimetypes
import os
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
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
    service: FileService = Depends(get_service),
    settings: Settings = Depends(get_settings),
):
    limit = settings.max_upload_bytes
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"File exceeds the {settings.max_upload_mb} MB limit.")
    if not data:
        raise HTTPException(400, "File is empty.")
    return _respond(service.process(data, file.filename or "upload", file.content_type, source="upload"))


class PathRequest(BaseModel):
    path: str


@app.post("/api/files/from-path", status_code=201, summary="Store a file from a local path on the server")
def upload_from_path(
    body: PathRequest,
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
    return _respond(service.process(p.read_bytes(), p.name, ctype, source="local-path"))


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
