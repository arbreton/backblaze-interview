# B2 Describe

Upload a file → it's stored in **Backblaze B2** → **Claude** writes a short caption (images) or summary (text/PDF) → the description is attached to the stored object as metadata and returned with the file name, size and status.

## Run
```bash
uv sync
cp .env.example .env        # fill in B2 values — see docs/B2_SETUP.md
uv run python cli.py --check
uv run uvicorn app.main:app --port 8000   # UI at http://localhost:8000, API docs at /docs
```
The Claude key is read from `~/Desktop/claudekey.txt` (or `ANTHROPIC_API_KEY`).

## curl
```bash
curl -F "file=@photo.jpg" localhost:8000/api/files
curl -H 'Content-Type: application/json' -d '{"path":"~/Desktop/notes.txt"}' localhost:8000/api/files/from-path
curl localhost:8000/api/files
curl -d '{"key":"uploads/…"}' -H 'Content-Type: application/json' localhost:8000/api/files/redescribe
```
Add `?stream=true` to the POST endpoints for live NDJSON progress.

Response: `filename, size, status (described|stored|failed), description, description_status, tags, key, url, timings_ms`.

## CLI
`uv run python cli.py file1.jpg file2.pdf`

## Tests
`uv run pytest` (21 tests, B2 mocked with moto, Claude faked).

## Docs
- `docs/DECISIONS.md` — why it's built this way (D1–D14)
- `docs/HISTORY.md` — what changed at each step (mirrors `git log`)
- `docs/B2_SETUP.md` — bucket and key setup

## Next ideas
Chunked summaries for very large files, auth on the API, streaming uploads for multi-GB files, batch re-describe.
