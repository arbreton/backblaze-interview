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

## Step 2 — Claude describer
- `app/describer.py`: detects the file type from the bytes (PDF magic, Pillow, UTF-8 sniff). Declared content-types and extensions aren't trusted.
  - image → caption (vision). Images over 1568 px or 4.5 MB are downscaled and re-encoded, and EXIF rotation is respected.
  - PDF → summary (native `document` block, up to 30 MB).
  - text → summary. Anything over 150k characters is cut, and the cut is *disclosed* to the model and flagged in the response (`truncated_input`), never silent (D8).
  - anything else → skipped, never an error.
- Structured output `{description, tags}`; the description is normalized and capped at 400 characters, tags deduplicated.
- Opus 5.5 at `effort=low`, server-side refusal fallback, typed error mapping (auth/rate-limit/bad-request/connection).
- Prompt-injection guard: the system prompt treats file content as data, not instructions.
- Tests: 11 passing (fake client). **Live check** with the real key: image caption 2.8 s (817→106 tokens), markdown summary 2.5 s. Both accurate.

## Step 3 — Service orchestration, JSON API, CLI
- `app/service.py` `FileService.process()`: **store → describe → attach**. Returns `filename, size, status, description, tags, key, url, timings_ms`, and the related fields.
  - `status`: `described` (stored and described) · `stored` (in B2, description skipped or failed) · `failed` (B2 upload failed).
  - `description_status`: `attached` · `skipped` · `failed`. The bucket also records `ai-status` so it reflects the outcome.
  - If Claude fails, the file is still stored and the error is recorded in metadata (`ai-error`).
  - `fit_metadata` shortens the description if the header budget is exceeded.
- `app/main.py` endpoints:
  - `POST /api/files` (multipart upload, size-capped)
  - `POST /api/files/from-path` (sandboxed to `LOCAL_PATH_ROOT`)
  - `GET /api/files` (recent files with metadata and presigned URLs)
  - `GET /api/health`
  - OpenAPI docs at `/docs`
  - If B2 isn't configured, the API returns a clear 503 that points to `docs/B2_SETUP.md`.
- `cli.py`: `cli.py <paths…>` and `cli.py --check`.
- Tests: 17 passing. Covers the end-to-end upload (metadata verified in the bucket), unsupported → skipped, refusal → still stored, 413, path sandbox (`/etc/hosts` and `..` → 403).
