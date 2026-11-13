"""Backblaze B2 storage via the S3-compatible API.

Metadata values are percent-encoded UTF-8 (B2 / S3 headers must be ASCII),
and decoded transparently on read.
"""

from __future__ import annotations

import re
import string
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import BinaryIO
from urllib.parse import quote, unquote

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .config import Settings

# S3 caps user metadata at 2 KB (key + value, all headers). Keep a safety margin.
METADATA_BUDGET_BYTES = 1900
_SAFE = "".join(c for c in string.printable if c not in "%\r\n\t\x0b\x0c")


class StorageError(RuntimeError):
    pass


def encode_meta(value: str) -> str:
    return quote(value, safe=_SAFE)


def decode_meta(value: str) -> str:
    return unquote(value)


def metadata_size(meta: dict[str, str]) -> int:
    return sum(len(k) + len(encode_meta(v)) for k, v in meta.items())


def safe_filename(name: str) -> str:
    name = name.replace("\\", "/").rsplit("/", 1)[-1].strip() or "file"
    name = re.sub(r"[^\w.\- ]+", "_", name, flags=re.UNICODE).strip(" .") or "file"
    return name[:120]


def build_key(prefix: str, filename: str, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"{prefix}{now:%Y/%m/%d}/{uuid.uuid4().hex[:8]}-{safe_filename(filename)}"


@dataclass
class StoredObject:
    key: str
    version_id: str | None
    size: int
    content_type: str
    metadata: dict[str, str]
    last_modified: datetime | None = None


class B2Storage:
    def __init__(self, settings: Settings, client=None):
        self.settings = settings
        self.bucket = settings.b2_bucket
        self.client = client or boto3.client(
            "s3",
            endpoint_url=settings.b2_endpoint_url,
            region_name=settings.b2_region,
            aws_access_key_id=settings.b2_key_id,
            aws_secret_access_key=settings.b2_application_key,
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 5, "mode": "standard"},
                # B2 does not need boto3's newer default CRC checksums; only send when required.
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )

    # --- helpers -------------------------------------------------------
    def _wrap(self, action: str, exc: Exception) -> StorageError:
        if isinstance(exc, ClientError):
            err = exc.response.get("Error", {})
            return StorageError(f"B2 {action} failed: {err.get('Code')} {err.get('Message', '')}".strip())
        return StorageError(f"B2 {action} failed: {exc}")

    # --- operations ----------------------------------------------------
    def check(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except (ClientError, BotoCoreError) as e:
            raise self._wrap("bucket check", e) from e

    def put(self, fileobj: BinaryIO, key: str, content_type: str, metadata: dict[str, str]) -> str | None:
        """Upload (multipart automatically for large files). Returns the version id."""
        try:
            self.client.upload_fileobj(
                fileobj,
                self.bucket,
                key,
                ExtraArgs={
                    "ContentType": content_type,
                    "Metadata": {k: encode_meta(v) for k, v in metadata.items()},
                },
            )
            return self.client.head_object(Bucket=self.bucket, Key=key).get("VersionId")
        except (ClientError, BotoCoreError) as e:
            raise self._wrap("upload", e) from e

    def replace_metadata(
        self, key: str, content_type: str, metadata: dict[str, str], delete_version: str | None = None
    ) -> str | None:
        """Attach metadata to an existing object via server-side copy-in-place.

        Optionally deletes the previous version so only one copy is kept.
        """
        try:
            resp = self.client.copy_object(
                Bucket=self.bucket,
                Key=key,
                CopySource={"Bucket": self.bucket, "Key": key},
                MetadataDirective="REPLACE",
                ContentType=content_type,
                Metadata={k: encode_meta(v) for k, v in metadata.items()},
            )
            new_version = resp.get("VersionId")
        except (ClientError, BotoCoreError) as e:
            raise self._wrap("metadata update", e) from e

        if delete_version and new_version and delete_version != new_version:
            try:
                self.client.delete_object(Bucket=self.bucket, Key=key, VersionId=delete_version)
            except (ClientError, BotoCoreError):
                pass  # non-fatal: an old version lingering only costs storage
        return new_version

    def head(self, key: str) -> StoredObject:
        try:
            h = self.client.head_object(Bucket=self.bucket, Key=key)
        except (ClientError, BotoCoreError) as e:
            raise self._wrap("head", e) from e
        return StoredObject(
            key=key,
            version_id=h.get("VersionId"),
            size=h["ContentLength"],
            content_type=h.get("ContentType", "application/octet-stream"),
            metadata={k: decode_meta(v) for k, v in h.get("Metadata", {}).items()},
            last_modified=h.get("LastModified"),
        )

    def list_recent(self, limit: int = 20) -> list[StoredObject]:
        """Most recent objects under the prefix, with their metadata."""
        try:
            paginator = self.client.get_paginator("list_objects_v2")
            items = []
            for page in paginator.paginate(Bucket=self.bucket, Prefix=self.settings.b2_prefix):
                items.extend(page.get("Contents", []))
        except (ClientError, BotoCoreError) as e:
            raise self._wrap("list", e) from e
        items.sort(key=lambda o: o["LastModified"], reverse=True)
        return [self.head(o["Key"]) for o in items[:limit]]

    def presigned_url(self, key: str, expires: int = 3600) -> str:
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires
        )
