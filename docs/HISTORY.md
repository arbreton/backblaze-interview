# Build history

One entry per step/iteration. Each step ends in a git commit (`git log --oneline` mirrors this file).

---

## Step 0 — Scaffold (2026-11-13)
- Created `~/Desktop/interview-backblaze`, `git init`, `uv` project (Python 3.13).
- Dependencies: fastapi, uvicorn, python-multipart, boto3, anthropic, pillow, pydantic-settings; dev: pytest, moto, httpx.
- `app/config.py`: settings from `.env`. The Claude key is resolved from `~/Desktop/claudekey.txt` (D4).
- `docs/B2_SETUP.md`: how to create the bucket and a least-privilege application key.
- `docs/DECISIONS.md` D1–D5 written up front.
