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

## D6 — Metadata encoding & size budget
**Decision:** Percent-encode metadata values (all printable ASCII except `%` stays readable) and keep the total under ~1.9 KB.
**Why:** S3/B2 user metadata travels as HTTP headers, so it has to be ASCII, and S3 caps it at 2 KB. Without encoding, a caption like "café" would break the upload. The description is therefore deliberately *short*. The full text is also returned to the caller.

## D7 — Object key scheme
**Decision:** `<prefix>YYYY/MM/DD/<uuid8>-<sanitized original name>`. The original filename is kept verbatim in metadata.
**Why:** Uploading the same filename twice never overwrites anything, the keys are browsable by date in the B2 UI, and the sanitization blocks `../` tricks.

## D8 — Large inputs: explicit truncation, not silent
**Decision:** Text over 150k characters (~40k tokens) is cut. The prompt tells the model it is reading only the beginning, and the API response returns `truncated_input: true`. Images are downscaled to a 1568 px long edge before they're sent. That's the resolution Claude's vision actually uses, so it costs less and runs faster with no loss of quality.
**Rejected:** Chunked map-reduce summarization of huge files. It would be more complete, but a "short description" doesn't need it, and it multiplies cost and latency. It's a possible later iteration.

## D9 — File type by content sniffing
**Decision:** Classify from the bytes rather than the client-supplied `Content-Type` or extension.
**Why:** Browsers send `application/octet-stream` for many files, and extensions lie. Sniffing is cheap and keeps us from sending binary junk to the model.
