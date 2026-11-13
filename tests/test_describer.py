import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from app.describer import DescribeError, Describer, _prepare_image, detect_kind


def test_detect_kind(png_bytes):
    assert detect_kind(png_bytes, "x.bin", "application/octet-stream") == "image"
    assert detect_kind(b"%PDF-1.7 ...", "a", None) == "pdf"
    assert detect_kind("héllo wörld".encode(), "notes", None) == "text"
    assert detect_kind(b"\x00\x01\x02binary", "a.txt", "text/plain") == "unsupported"
    assert detect_kind(b"", "empty.txt", "text/plain") == "unsupported"


def test_large_image_is_downscaled():
    buf = io.BytesIO()
    Image.new("RGB", (4000, 3000), (10, 120, 200)).save(buf, format="PNG")
    media, b64 = _prepare_image(buf.getvalue())
    assert media == "image/jpeg"
    import base64

    with Image.open(io.BytesIO(base64.b64decode(b64))) as im:
        assert max(im.size) == 1568


def test_small_image_passthrough(png_bytes):
    media, _ = _prepare_image(png_bytes)
    assert media == "image/png"


class FakeMessages:
    def __init__(self, payload, stop_reason="end_turn"):
        self.payload, self.stop_reason, self.calls = payload, stop_reason, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            content=[SimpleNamespace(type="text", text=json.dumps(self.payload))],
            model=kwargs["model"],
            usage=SimpleNamespace(input_tokens=100, output_tokens=20),
        )


def fake_client(payload, stop_reason="end_turn"):
    msgs = FakeMessages(payload, stop_reason)
    return SimpleNamespace(beta=SimpleNamespace(messages=msgs)), msgs


def test_describe_text_truncates_and_normalizes(settings):
    client, msgs = fake_client({"description": "  A   list of\nthings. " + "x" * 500, "tags": ["Todo", "todo", "List"]})
    d = Describer(settings, client=client).describe(b"a" * 200_000, "big.txt", "text")
    assert d.kind == "summary"
    assert d.truncated_input is True
    assert len(d.text) <= 400 and d.text.startswith("A list of things.")
    assert d.tags == ["todo", "list"]
    req = msgs.calls[0]
    assert req["output_config"]["effort"] == "low"
    assert req["fallbacks"] == "default"
    assert "only the first" in req["messages"][0]["content"][0]["text"]


def test_describe_refusal(settings):
    client, _ = fake_client({}, stop_reason="refusal")
    with pytest.raises(DescribeError, match="declined"):
        Describer(settings, client=client).describe(b"hi", "a.txt", "text")


def test_describe_image_sends_image_block(settings, png_bytes):
    client, msgs = fake_client({"description": "A red square.", "tags": ["red"]})
    d = Describer(settings, client=client).describe(png_bytes, "r.png", "image")
    assert d.kind == "caption"
    assert msgs.calls[0]["messages"][0]["content"][0]["type"] == "image"
