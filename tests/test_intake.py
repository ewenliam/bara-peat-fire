"""Tests for the Claude-written intake pipeline in src/intake.py.

Every Muse-backed step is replaced with a fake, so no API call is made.

Usage:
    python tests/test_intake.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
TEST_ENV = Path(tempfile.mkdtemp()) / ".env"  # never the real .env
TEST_ENV.write_text("\n".join(["WA_ALLOWED_SENDERS=48111, 48222", "WA_ACCESS_TOKEN=wa-test-token",
                               "META_API_KEY=meta-test-key", "META_BASE_URL=https://example.test/v1",
                               "FFMPEG=ffmpeg-test"]) + "\n", encoding="utf-8")
os.environ["WA_ACCESS_TOKEN"] = "stale-env-token"  # a stale process value must lose to the file

import intake  # noqa: E402

intake.ENV_FILE = TEST_ENV
from transcribe import TranscribeError  # noqa: E402
from whatsapp_media import MediaError  # noqa: E402

CALLS: list[tuple] = []
GOOD = {"report": {"fire_state": "smouldering_peat"}, "error": None, "grounding_problems": [], "missing": [],
        "grounded": True, "cost_usd": 0.007, "llm_calls": [{"tag": "report extract"}]}


def fresh() -> None:
    d = Path(tempfile.mkdtemp())
    intake.REPORTS = d / "reports.jsonl"
    intake.INBOX = d / "inbox.jsonl"
    intake.ROOT = d
    intake.MEDIA_DIR = d / "media"
    intake.WAV_DIR = d / "wav"
    CALLS.clear()
    intake.download_media = fake_download
    intake.ogg_to_wav = lambda src, dest, ffmpeg: CALLS.append(("wav", src, dest, ffmpeg)) or dest / "n.wav"
    intake.transcribe = fake_transcribe
    intake.extract = lambda text, place_hints: CALLS.append(("extract", text, place_hints)) or dict(GOOD)


def fake_download(media_id, token, media_dir, expected_sha256=None):
    CALLS.append(("download", media_id, token, media_dir, expected_sha256))
    ext = ".ogg" if media_id == "111" else ".jpg"
    return {"path": media_dir / f"{media_id}{ext}", "mime_type": "x", "bytes": 3, "sha256": "h", "cached": False}


def fake_transcribe(wav, api_key, keywords, languages, base_url):
    CALLS.append(("transcribe", wav, api_key, tuple(keywords), tuple(languages), base_url))
    return {"transcript": "smoke rising from the peat", "session_id": "s", "audio_ms": 3000,
            "cost_usd": 0.00015, "keywords": keywords}


def row(kind: str, sender: str = "48111", msg_id: str = "wamid.A") -> dict:
    bodies = {"audio": {"id": "111", "mime_type": "audio/ogg; codecs=opus", "sha256": "b64"},
              "image": {"id": "222", "mime_type": "image/jpeg", "sha256": "b64i", "caption": "still smoking"},
              "text": {"body": "Smoke near Kalampangan"}, "sticker": {"id": "333"}}
    return {"id": msg_id, "from": sender, "type": kind, "received_at": "t",
            "message": {"id": msg_id, "type": kind, kind: bodies[kind]}}


def stored() -> list[dict]:
    return [json.loads(line) for line in intake.REPORTS.read_text(encoding="utf-8").splitlines()]


def test_audio_chain():
    fresh()
    out = intake.process(row("audio"))
    assert [c[0] for c in CALLS] == ["download", "wav", "transcribe", "extract"], CALLS
    assert CALLS[0][1:] == ("111", "wa-test-token", intake.MEDIA_DIR, "b64")
    assert CALLS[1][3] == "ffmpeg-test"
    assert CALLS[2][2] == "meta-test-key" and CALLS[2][5] == "https://example.test/v1"
    assert "peat" in CALLS[2][3] and "Kalampangan" in CALLS[2][3] and CALLS[2][4] == ("English",)
    assert CALLS[3][1] == "smoke rising from the peat"
    assert out["status"] == "ok" and out["transcript"] == "smoke rising from the peat"
    assert out["media"]["path"] == "media/111.ogg" and out["transcribe_cost_usd"] == 0.00015
    assert "llm_calls" not in out["extraction"] and out["extraction"]["grounded"] is True
    assert stored() == [json.loads(json.dumps(out))]


def test_text_and_image():
    fresh()
    t = intake.process(row("text", msg_id="wamid.T"))
    assert [c[0] for c in CALLS] == ["extract"] and t["transcript"] == "Smoke near Kalampangan"
    CALLS.clear()
    i = intake.process(row("image", msg_id="wamid.I"))
    assert [c[0] for c in CALLS] == ["download"] and i["status"] == "ok" and i["extraction"] is None
    assert i["media"]["caption"] == "still smoking"


def test_unknown_sender_skipped_without_calls():
    fresh()
    out = intake.process(row("audio", sender="99999"))
    assert out["status"] == "skipped" and CALLS == [] and len(stored()) == 1


def test_unsupported_type():
    fresh()
    out = intake.process(row("sticker"))
    assert out["status"] == "unsupported" and CALLS == []


def test_ungrounded_needs_review():
    fresh()
    intake.extract = lambda text, place_hints: {**GOOD, "grounded": False, "grounding_problems": ["x"]}
    assert intake.process(row("text"))["status"] == "needs_review"


def test_expected_errors_recorded():
    for exc in (MediaError("download failed 404"), TranscribeError("ffmpeg not found")):
        fresh()

        def boom(*a, **k):
            raise exc

        if isinstance(exc, MediaError):
            intake.download_media = boom
        else:
            intake.ogg_to_wav = boom
        out = intake.process(row("audio"))
        assert out["status"] == "error" and type(exc).__name__ in out["error"], out
        assert stored()[0]["status"] == "error"


def test_unexpected_errors_propagate():
    fresh()

    def bug(*a, **k):
        raise ZeroDivisionError("bug")

    intake.extract = bug
    try:
        intake.process(row("text"))
    except ZeroDivisionError:
        return
    raise AssertionError("a bug must not be recorded as a normal failure")


def test_process_rows_skips_done_ids():
    fresh()
    intake.process(row("text", msg_id="wamid.1"))
    CALLS.clear()
    out = intake.process_rows([row("text", msg_id="wamid.1"), row("text", msg_id="wamid.2")])
    assert [r["id"] for r in out] == ["wamid.2"] and len(CALLS) == 1


def test_error_rows_are_retried():
    fresh()
    def expired_token(*a, **k):
        raise MediaError("metadata failed 401")

    intake.download_media = expired_token
    assert intake.process(row("image", msg_id="wamid.E"))["status"] == "error"
    assert "wamid.E" not in intake.processed_ids()
    intake.download_media = fake_download
    out = intake.process_rows([row("image", msg_id="wamid.E")])
    assert [r["status"] for r in out] == ["ok"] and "wamid.E" in intake.processed_ids()


def test_browser_upload_runs_same_steps():
    fresh()
    intake.UPLOAD_DIR = intake.ROOT / "uploads"
    out = intake.process_upload(b"webm audio bytes", ".webm")
    assert [c[0] for c in CALLS] == ["wav", "transcribe", "extract"], CALLS
    assert out["source"] == "browser" and out["id"].startswith("web.") and out["status"] == "ok"
    assert (intake.ROOT / out["media"]["path"]).read_bytes() == b"webm audio bytes"
    assert stored()[0]["id"] == out["id"]


def test_process_pending_reads_inbox():
    fresh()
    intake.INBOX.write_text(json.dumps(row("text", msg_id="wamid.P")) + "\n", encoding="utf-8")
    assert [r["id"] for r in intake.process_pending()] == ["wamid.P"]
    assert intake.process_pending() == []


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
