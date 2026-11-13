import io

from app.storage import build_key, decode_meta, encode_meta, safe_filename


def test_meta_roundtrip_unicode():
    s = "Un café à Montréal — 100% ☕\nnew line"
    enc = encode_meta(s)
    assert enc.isascii() and "\n" not in enc
    assert decode_meta(enc) == s


def test_safe_filename():
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("C:\\x\\my photo (1).jpg") == "my photo _1_.jpg"
    assert safe_filename("") == "file"


def test_build_key_shape():
    k = build_key("uploads/", "a b.txt")
    assert k.startswith("uploads/") and k.endswith("-a b.txt")


def test_put_then_replace_metadata_keeps_single_version(storage):
    key = "uploads/x/hello.txt"
    v1 = storage.put(io.BytesIO(b"hello"), key, "text/plain", {"original-filename": "hello.txt"})
    v2 = storage.replace_metadata(
        key,
        "text/plain",
        {"original-filename": "hello.txt", "ai-description": "A greeting — ☀"},
        delete_version=v1,
    )
    assert v2 and v2 != v1

    obj = storage.head(key)
    assert obj.metadata["ai-description"] == "A greeting — ☀"
    assert obj.size == 5

    versions = storage.client.list_object_versions(Bucket=storage.bucket, Prefix=key)["Versions"]
    assert len(versions) == 1


def test_list_recent(storage):
    for i in range(3):
        storage.put(io.BytesIO(b"x"), f"uploads/f{i}.txt", "text/plain", {"n": str(i)})
    items = storage.list_recent(limit=2)
    assert len(items) == 2
