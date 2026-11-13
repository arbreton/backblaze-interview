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

## Step 4 — Web UI
- `app/static/{index.html,app.css,app.js}`: vanilla HTML/CSS/JS with no build step, served by FastAPI at `/`.
- UX:
  - Drag and drop, a file picker, multi-file uploads (2 at a time), and paste-to-upload (⌘V).
  - Real upload progress (XHR), then an explicit "Storing in B2 and generating description…" phase.
  - Result cards show: status badge (Described / Stored / Failed), size, type, total time, description, tags, Copy and Open (presigned URL) buttons.
  - "Local path" tab, which uses the sandboxed endpoint.
  - "In the bucket" list reads metadata back from B2, which proves it was attached.
  - Health pills plus a setup banner that names the missing `.env` keys when B2 isn't configured.
  - curl snippet panel.
- Accessibility:
  - Semantic landmarks and a skip link.
  - WAI-ARIA tabs with arrow-key navigation.
  - Visible focus rings, 44 px touch targets, `aria-live` announcements for results, `role=progressbar`.
  - Status is conveyed by text, not just color.
  - `prefers-color-scheme` dark mode and `prefers-reduced-motion`.
- Security: every server or user string is rendered via `textContent`, never `innerHTML`.
- **Verification:**
  - End-to-end over HTTP against a local S3 emulator (`moto_server`) with the **real Claude API**. Image caption 2.5 s, text summary 2.8 s. Unsupported binary → stored and skipped. Path-traversal attempt → 403. 1 version per object after the metadata update.
  - Headless Chrome (Playwright) drove the real UI: light at 1100 px and dark at 390 px. 0 console errors, no horizontal scroll.
  - Fix from that review: on mobile, non-image files got a large empty thumbnail; now only real image previews expand.

## Step 5 — Live on real Backblaze B2
- Bucket `ai-interview-ariane-breton` (private, `s3.us-east-005.backblazeb2.com`, SSE enabled, lifecycle "keep all versions"). Credentials are in `.env` (git-ignored, `chmod 600`).
- `cli.py --check` → bucket reachable, Claude key found.
- Real uploads: store ~0.2–0.7 s, describe ~2.4 s, attach (server-side copy) ~0.4–0.5 s, **total ~3–3.7 s**.
- Verified on B2 itself:
  - Metadata reads back via `HeadObject` (`ai-description`, `ai-tags`, `ai-kind`, `ai-model`, `ai-status`, `sha256`...).
  - **One version per object** even under "keep all versions" (pre-description version deleted).
  - UTF-8 filename and description (`recette café.txt`, `65 °C`, `☕`) round-trip correctly (D6 works on real B2).
  - Presigned URL downloads the original bytes.
- UI fix found during the live review: bucket thumbnails were blank until B2 answered. They're now lazy `<img>` elements over a file-type label, so a slow or failed preview falls back to "JPG" and never shows a blank box.

## Iteration 6 — Honest progress: streamed stages (NDJSON)
- **Problem seen in Step 5:** after the upload hit 100%, the UI showed one vague "Storing and describing…" spinner for about 3 s, even though the file was safely in B2 within ~1 s.
- `FileService.process(on_stage=…)` emits `storing → stored → describing → attaching`.
- `POST /api/files?stream=true` (and `/from-path?stream=true`) returns `application/x-ndjson`: one stage event per line, then `{"event":"result",…}`. Without `stream`, curl users get the same single JSON response as before (backwards compatible).
- UI: XHR `onprogress` / fetch `ReadableStream` feed one incremental NDJSON parser. Copy now says "Saved to B2 ✓ — Claude is writing a description…", so the user knows the file is safe before the AI step ends.
- Live timing on B2 (`curl -N`): stored at +0.98 s, attaching at +3.59 s, result at +3.84 s.
- Headless Chrome confirmed the stage text sequence in the real UI, with 0 JS errors.
- **Bug fix:** if both the description *and* writing the failure marker failed, the error said "Description generated but could not be attached". It now keeps the real cause. A regression test was added.
- Tests: 19 passing.
