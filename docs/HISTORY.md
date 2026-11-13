# Build history

One entry per step/iteration. Each step ends in a git commit (`git log --oneline` mirrors this file).

---

## Step 0 — Scaffold (2026-11-13)
- Created `~/Desktop/interview-backblaze`, `git init`, `uv` project (Python 3.13).
- Dependencies: fastapi, uvicorn, python-multipart, boto3, anthropic, pillow, pydantic-settings; dev: pytest, moto, httpx.
- `app/config.py`: settings from `.env`. The Claude key is resolved from `~/Desktop/claudekey.txt` (D4).
- `docs/B2_SETUP.md`: how to create the bucket and a least-privilege application key.
- `docs/DECISIONS.md` D1–D5 written up front.

## Step 1 — B2 storage layer
- `app/storage.py`: `B2Storage` wraps boto3 (S3-compatible endpoint): `put` (multipart via `upload_fileobj`), `replace_metadata` (copy-in-place, then delete the old version), `head`, `list_recent`, `presigned_url`, `check`.
- Metadata values are percent-encoded UTF-8 so accents and emoji survive ASCII-only headers (D6).
- Object keys: `uploads/YYYY/MM/DD/<8-hex>-<sanitized-name>`. No collisions, no path traversal (D7).
- boto3's newer default CRC checksums are turned off (`when_required`) for B2 compatibility.
- Tests (moto, versioned bucket like B2): 5 passing. Includes "only one version remains after the metadata update".
