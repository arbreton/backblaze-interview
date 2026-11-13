import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import get_settings
from app.describer import Describer
from app.service import FileService
from tests.test_describer import fake_client


@pytest.fixture
def client(settings, storage):
    fake, msgs = fake_client({"description": "A small red rectangle.", "tags": ["red", "shape"]})
    service = FileService(storage, Describer(settings, client=fake))
    main.app.dependency_overrides[main.get_service] = lambda: service
    main.app.dependency_overrides[get_settings] = lambda: settings
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def test_upload_image_end_to_end(client, storage, png_bytes):
    r = client.post("/api/files", files={"file": ("red.png", png_bytes, "image/png")})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "described"
    assert body["description_status"] == "attached"
    assert body["filename"] == "red.png" and body["size"] == len(png_bytes)
    assert body["description"] == "A small red rectangle."

    meta = storage.head(body["key"]).metadata
    assert meta["ai-description"] == "A small red rectangle."
    assert meta["ai-tags"] == "red, shape"
    assert meta["original-filename"] == "red.png"

    listed = client.get("/api/files").json()
    assert listed[0]["description"] == "A small red rectangle."


def test_unsupported_file_is_stored_but_skipped(client):
    r = client.post("/api/files", files={"file": ("blob.bin", b"\x00\x01\x02", "application/octet-stream")})
    body = r.json()
    assert r.status_code == 201
    assert body["status"] == "stored" and body["description_status"] == "skipped"


def test_describe_failure_still_stores(settings, storage):
    fake, _ = fake_client({}, stop_reason="refusal")
    service = FileService(storage, Describer(settings, client=fake))
    res = service.process(b"hello world", "a.txt", "text/plain")
    assert res.status == "stored" and res.description_status == "failed"
    assert storage.head(res.key).metadata["ai-status"] == "failed"


def test_too_large(client):
    r = client.post("/api/files", files={"file": ("big.txt", b"a" * (1024 * 1024 + 1), "text/plain")})
    assert r.status_code == 413


def test_from_path_sandboxed(client, settings, tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("Buy milk and eggs.")
    ok = client.post("/api/files/from-path", json={"path": str(f)})
    assert ok.status_code == 201 and ok.json()["status"] == "described"

    denied = client.post("/api/files/from-path", json={"path": "/etc/hosts"})
    assert denied.status_code == 403
    traversal = client.post("/api/files/from-path", json={"path": str(tmp_path / ".." / "x")})
    assert traversal.status_code == 403


def test_health(client):
    h = client.get("/api/health").json()
    assert h["claude"]["configured"] is True


def test_stream_emits_stages_then_result(client, png_bytes):
    import json

    r = client.post("/api/files?stream=true", files={"file": ("red.png", png_bytes, "image/png")})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(line) for line in r.text.splitlines() if line]
    stages = [e["stage"] for e in events if e["event"] == "stage"]
    assert stages == ["storing", "stored", "describing", "attaching"]
    assert events[-1]["event"] == "result"
    assert events[-1]["result"]["status"] == "described"


def test_describe_and_attach_both_fail_keeps_real_error(settings, storage, monkeypatch):
    fake, _ = fake_client({}, stop_reason="refusal")
    service = FileService(storage, Describer(settings, client=fake))

    def boom(*a, **k):
        raise RuntimeError("b2 down")

    monkeypatch.setattr(storage, "replace_metadata", boom)
    res = service.process(b"hello", "a.txt", "text/plain")
    assert res.status == "stored" and res.description_status == "failed"
    assert "declined" in res.error


def test_redescribe_fixes_failed_file_without_reupload(client, settings, storage):
    # First attempt: Claude declines -> stored, description failed.
    bad, _ = fake_client({}, stop_reason="refusal")
    res = FileService(storage, Describer(settings, client=bad)).process(b"Quarterly plan.", "plan.txt", "text/plain")
    assert res.description_status == "failed"
    assert storage.head(res.key).metadata["ai-error"]

    # Retry through the API (fixture's describer works).
    r = client.post("/api/files/redescribe", json={"key": res.key})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "described" and body["key"] == res.key
    meta = storage.head(res.key).metadata
    assert meta["ai-status"] == "attached"
    assert "ai-error" not in meta                     # stale failure info removed
    assert meta["sha256"] == res.sha256               # provenance preserved
    versions = storage.client.list_object_versions(Bucket=storage.bucket, Prefix=res.key)["Versions"]
    assert len(versions) == 1


def test_redescribe_rejects_foreign_or_missing_keys(client):
    assert client.post("/api/files/redescribe", json={"key": "secrets/x"}).status_code == 400
    assert client.post("/api/files/redescribe", json={"key": "uploads/../x"}).status_code == 400
    assert client.post("/api/files/redescribe", json={"key": "uploads/nope.txt"}).status_code == 404
