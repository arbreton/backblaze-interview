"""Orchestration: store -> describe -> attach description as metadata."""

from __future__ import annotations

import hashlib
import io
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Literal

from .describer import Describer, DescribeError, Description, detect_kind
from .storage import METADATA_BUDGET_BYTES, B2Storage, build_key, metadata_size

Status = Literal["described", "stored", "failed"]
DescStatus = Literal["attached", "skipped", "failed", "pending"]


@dataclass
class Result:
    filename: str
    size: int
    content_type: str
    status: Status
    description_status: DescStatus
    description: str | None = None
    description_kind: str | None = None
    tags: list[str] = field(default_factory=list)
    model: str | None = None
    truncated_input: bool = False
    error: str | None = None
    key: str | None = None
    bucket: str | None = None
    version_id: str | None = None
    sha256: str | None = None
    url: str | None = None
    timings_ms: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fit_metadata(base: dict[str, str], desc: Description) -> dict[str, str]:
    """Add AI metadata, shortening the description if the 2 KB header budget requires it."""
    meta = dict(base)
    meta.update({
        "ai-status": "attached",
        "ai-kind": desc.kind,
        "ai-model": desc.model,
        "ai-described-at": _now(),
        "ai-tags": ", ".join(desc.tags),
        "ai-description": desc.text,
    })
    text = desc.text
    while metadata_size(meta) > METADATA_BUDGET_BYTES and len(text) > 40:
        text = text[: int(len(text) * 0.85)].rsplit(" ", 1)[0] + "…"
        meta["ai-description"] = text
    return meta


class FileService:
    def __init__(self, storage: B2Storage, describer: Describer):
        self.storage = storage
        self.describer = describer

    def process(self, data: bytes, filename: str, content_type: str | None, source: str = "upload") -> Result:
        t0 = time.perf_counter()
        ms = lambda since: int((time.perf_counter() - since) * 1000)  # noqa: E731

        kind = detect_kind(data, filename, content_type)
        content_type = content_type or "application/octet-stream"
        if content_type == "application/octet-stream":
            content_type = {"pdf": "application/pdf", "text": "text/plain; charset=utf-8"}.get(kind, content_type)

        sha = hashlib.sha256(data).hexdigest()
        key = build_key(self.storage.settings.b2_prefix, filename)
        base_meta = {
            "original-filename": filename[:200],
            "sha256": sha,
            "uploaded-at": _now(),
            "source": source,
            "ai-status": "pending" if kind != "unsupported" else "skipped",
        }
        result = Result(
            filename=filename, size=len(data), content_type=content_type, status="failed",
            description_status="pending", key=key, bucket=self.storage.bucket, sha256=sha,
        )

        # 1) Store — the file is safe in B2 before any AI work happens.
        t = time.perf_counter()
        try:
            version = self.storage.put(io.BytesIO(data), key, content_type, base_meta)
        except Exception as e:  # StorageError or unexpected
            result.error = str(e)
            result.description_status = "skipped"
            result.timings_ms = {"store": ms(t), "total": ms(t0)}
            return result
        result.status, result.version_id = "stored", version
        result.timings_ms["store"] = ms(t)

        # 2) Describe
        if kind == "unsupported":
            result.description_status = "skipped"
            result.error = "No AI description: file type not supported (images, PDFs and text are)."
        elif not self.describer.configured:
            result.description_status = "skipped"
            result.error = "No AI description: Claude API key not configured."
        else:
            t = time.perf_counter()
            try:
                desc = self.describer.describe(data, filename, kind)
                result.timings_ms["describe"] = ms(t)
            except DescribeError as e:
                result.timings_ms["describe"] = ms(t)
                result.description_status, result.error = "failed", str(e)
                desc = None

            # 3) Attach — server-side copy-in-place, then drop the pre-description version.
            if desc:
                meta = fit_metadata(base_meta, desc)
            else:
                meta = {**base_meta, "ai-status": "failed", "ai-error": (result.error or "")[:200]}
            t = time.perf_counter()
            try:
                result.version_id = self.storage.replace_metadata(key, content_type, meta, delete_version=version)
                if desc:
                    result.status, result.description_status = "described", "attached"
                    result.description = meta["ai-description"]
            except Exception as e:
                result.description_status = "failed"
                result.error = f"Description generated but could not be attached: {e}"
            if desc:
                # Return the description even if attaching failed — the caller still gets value.
                result.description = result.description or desc.text
                result.description_kind, result.tags = desc.kind, desc.tags
                result.model, result.truncated_input = desc.model, desc.truncated_input
            result.timings_ms["attach"] = ms(t)

        try:
            result.url = self.storage.presigned_url(key)
        except Exception:
            pass
        result.timings_ms["total"] = ms(t0)
        return result
