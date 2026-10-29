"""Acceptance tests for src/whatsapp_media.py (written by Claude, see specs/whatsapp_media.md).

No network: a fake session replays scripted responses.

Usage:
    python tests/test_whatsapp_media.py
"""
import base64
import hashlib
import inspect
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import whatsapp_media  # noqa: E402
from whatsapp_media import MediaError, download_media, media_path, verify_sha256  # noqa: E402

TOKEN = "test-token-abc123"
DATA = b"OggS" + bytes(range(256)) * 4
HEX = hashlib.sha256(DATA).hexdigest()
B64 = base64.b64encode(hashlib.sha256(DATA).digest()).decode()
SIGNED_URL = "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=123&hash=SECRETHASH"


class Resp:
    def __init__(self, status: int, json_body: dict | None = None, content: bytes = b""):
        self.status_code = status
        self._json = json_body
        self.content = content

    def json(self):
        return self._json


class FakeSession:
    def __init__(self, responses: list):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, headers, timeout))
        return self.responses.pop(0)


def meta(size: int = len(DATA), sha: str = HEX, mime: str = "audio/ogg", url: str = SIGNED_URL) -> Resp:
    return Resp(200, {"messaging_product": "whatsapp", "id": "2708226019592949", "url": url,
                      "mime_type": mime, "sha256": sha, "file_size": size})


def tmpdir() -> Path:
    return Path(tempfile.mkdtemp()) / "media"


def expect_error(fn, *args, **kwargs) -> str:
    try:
        fn(*args, **kwargs)
    except MediaError as e:
        return str(e)
    raise AssertionError("expected MediaError")


def test_verify_sha256_formats():
    assert verify_sha256(DATA, HEX) and verify_sha256(DATA, HEX.upper()) and verify_sha256(DATA, B64)
    for bad in ["", "abc", HEX[:-1], B64[:-4], "z" * 64, hashlib.sha256(b"other").hexdigest(),
                "é" * 64, "é" * 44, None]:
        assert verify_sha256(DATA, bad) is False, bad


def test_media_path_and_extensions():
    d = Path("m")
    assert media_path(d, "123", "audio/ogg; codecs=opus") == d / "123.ogg"
    assert media_path(d, "123", "IMAGE/JPEG") == d / "123.jpg"
    assert media_path(d, "9", "image/png") == d / "9.png"
    for bad_id in ["", "../x", "12a", "1/2", "١٢"]:
        expect_error(media_path, d, bad_id, "audio/ogg")
    expect_error(media_path, d, "123", "video/mp4")


def test_download_happy_path():
    d = tmpdir()
    s = FakeSession([meta(), Resp(200, content=DATA)])
    out = download_media("2708226019592949", TOKEN, d, expected_sha256=B64, session=s)
    assert out == {"path": d / "2708226019592949.ogg", "mime_type": "audio/ogg", "bytes": len(DATA),
                   "sha256": HEX, "cached": False}
    assert out["path"].read_bytes() == DATA
    assert s.calls[0] == ("https://graph.facebook.com/2708226019592949",
                          {"Authorization": f"Bearer {TOKEN}"}, 30)
    assert s.calls[1] == (SIGNED_URL, {"Authorization": f"Bearer {TOKEN}"}, 30)
    assert not list(d.glob("*.part"))


def test_cached_file_skips_download():
    d = tmpdir()
    download_media("2708226019592949", TOKEN, d, session=FakeSession([meta(), Resp(200, content=DATA)]))
    s = FakeSession([meta()])
    out = download_media("2708226019592949", TOKEN, d, session=s)
    assert out["cached"] is True and len(s.calls) == 1


def test_expired_url_refreshed_once():
    d = tmpdir()
    s = FakeSession([meta(), Resp(404), meta(url=SIGNED_URL + "2"), Resp(200, content=DATA)])
    out = download_media("2708226019592949", TOKEN, d, session=s)
    assert out["path"].read_bytes() == DATA and len(s.calls) == 4
    assert s.calls[3][0] == SIGNED_URL + "2"
    s = FakeSession([meta(), Resp(404), meta(), Resp(404)])
    msg = expect_error(download_media, "2708226019592949", TOKEN, tmpdir(), session=s)
    assert "404" in msg


def test_failures_raise_and_leave_nothing():
    cases = [
        [Resp(401, {"error": {"message": "bad token"}})],
        [meta(), Resp(500)],
        [meta(size=len(DATA) + 1), Resp(200, content=DATA)],
        [meta(sha=hashlib.sha256(b"other").hexdigest()), Resp(200, content=DATA)],
        [meta(mime="video/mp4")],
    ]
    for responses in cases:
        d = tmpdir()
        msg = expect_error(download_media, "2708226019592949", TOKEN, d, session=FakeSession(responses))
        assert TOKEN not in msg and "SECRETHASH" not in msg and SIGNED_URL not in msg, msg
        assert not (d.exists() and any(d.iterdir())), list(d.iterdir())
    assert "401" in expect_error(download_media, "2708226019592949", TOKEN, tmpdir(),
                                 session=FakeSession([Resp(401, {})]))


def test_webhook_hash_mismatch_raises():
    other_b64 = base64.b64encode(hashlib.sha256(b"other").digest()).decode()
    expect_error(download_media, "2708226019592949", TOKEN, tmpdir(), expected_sha256=other_b64,
                 session=FakeSession([meta(), Resp(200, content=DATA)]))


def test_too_large_raises():
    big = b"x" * (whatsapp_media.MAX_BYTES + 1)
    s = FakeSession([meta(size=len(big), sha=hashlib.sha256(big).hexdigest()), Resp(200, content=big)])
    expect_error(download_media, "2708226019592949", TOKEN, tmpdir(), session=s)


def test_bad_media_id_makes_no_request():
    s = FakeSession([])
    expect_error(download_media, "../../etc", TOKEN, tmpdir(), session=s)
    assert s.calls == []


def test_request_exception_does_not_leak_url():
    import requests

    class Exploding(FakeSession):
        def get(self, url, headers=None, timeout=None):
            self.calls.append((url, headers, timeout))
            if url == SIGNED_URL:
                raise requests.ConnectionError(f"connection failed for {SIGNED_URL}")
            return self.responses.pop(0)

    try:
        download_media("2708226019592949", TOKEN, tmpdir(), session=Exploding([meta()]))
    except MediaError as e:
        assert "SECRETHASH" not in str(e) and TOKEN not in str(e), str(e)
        assert e.__cause__ is None and e.__suppress_context__ is True
        return
    raise AssertionError("expected MediaError")


def test_module_rules():
    assert whatsapp_media.MAX_BYTES == 16 * 1024 * 1024
    src = inspect.getsource(whatsapp_media)
    assert "os.environ" not in src and "getenv" not in src and "print(" not in src
    assert "except Exception" not in src and "except:" not in src
    assert len(src.splitlines()) <= 140, len(src.splitlines())
    assert "compare_digest" in src
    assert chr(0x2014) not in src  # no em dashes


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"pass  {name}")
        except Exception as e:  # report every failure, not just the first
            failed += 1
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print("all passed" if not failed else f"{failed} of {len(tests)} failed")
    sys.exit(1 if failed else 0)
