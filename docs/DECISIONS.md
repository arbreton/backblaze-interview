# Decision log

ADR-lite. Each entry records the context, the decision, and the alternatives we rejected. New entries go at the bottom.

---

## D1 — Language & framework: Python + FastAPI
**Context:** The brief says "a minimal HTML page, a curl-able endpoint — your choice". The user asked for a UI.
**Decision:** Python 3.13, FastAPI, uvicorn, managed with `uv`. One process serves both the JSON API (curl-able) and a static HTML/JS UI.
**Why:** FastAPI gives typed request/response models and OpenAPI docs at `/docs` for free, and multipart upload support is built in. The Anthropic Python SDK and boto3 are both first-class.
**Rejected:** Node/Express, which is equally viable but has no particular advantage here. A separate SPA build (React/Vite) adds a build step for a single page, so it's overkill.

## D2 — B2 access via the S3-compatible API (boto3), not the native b2sdk
**Decision:** `boto3` S3 client pointed at the bucket's S3 endpoint.
**Why:** It's the most widely known storage API, so the code is portable and familiar to reviewers. `upload_fileobj` handles multipart for large files. Metadata maps to `x-amz-meta-*`, which B2 shows as File Info.
**Rejected:** `b2sdk` (native API), which is also good but more B2-specific.

## D3 — "Attach after storing" = server-side copy-in-place
**Context:** The brief says: store the file, *then* describe it and attach the description as metadata. S3/B2 object metadata is immutable.
**Decision:** (1) `PutObject` with base metadata → (2) describe with Claude → (3) `CopyObject` onto the same key with `MetadataDirective=REPLACE` → (4) delete the pre-description version.
**Why:** It follows the brief literally. The file is durably stored even if the AI step fails (status `stored`, `description_status=failed`). The copy runs server-side in B2, so it doesn't re-upload the bytes.
**Trade-off:** One extra API call, and a second version until step 4 runs. Alternative: describe first, upload once. That's cheaper, but if Claude is slow or down the file isn't stored, which inverts the priority.

## D4 — Claude key read from `~/Desktop/claudekey.txt` at runtime
**Decision:** `ANTHROPIC_API_KEY` env var wins if set. Otherwise the key is read from `CLAUDE_KEY_FILE` (default `~/Desktop/claudekey.txt`). The key is never copied into the repo, never logged, and never sent to the browser. `.env` and `claudekey.txt` are git-ignored.

## D5 — Model: `claude-opus-5-5`, low effort, structured output
**Decision:** Claude Opus 5.5 with `output_config.effort="low"` and a JSON-schema structured output `{kind, description, tags}`.
**Why:** It's the current default model. Captions and short summaries are simple tasks, so low effort keeps latency and cost down without changing model. Structured output removes fragile string parsing. `tags` comes almost free and makes useful metadata. The model is configurable via `CLAUDE_MODEL`.
**Refusals:** Opus 5.5 can decline (`stop_reason="refusal"`). We opt into server-side fallbacks (`fallbacks="default"`, beta `server-side-fallback-2026-07-01`) and still check `stop_reason` before reading the content.
