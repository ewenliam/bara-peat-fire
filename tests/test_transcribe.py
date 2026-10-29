"""Acceptance tests for src/transcribe.py (written by Claude, see specs/transcribe.md).

No API calls: a fake session stands in for requests and the ledger is replaced. The ffmpeg
tests use the real ffmpeg from the FFMPEG environment variable or PATH.

Usage:
    python tests/test_transcribe.py
"""
import inspect
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import transcribe as tr  # noqa: E402
from llm import BudgetExceeded  # noqa: E402
from transcribe import TranscribeError, ogg_to_wav, transcribe, wav_info  # noqa: E402

FFMPEG = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
KEY = "test-api-key-xyz"
LEDGER: list[dict] = []
BUDGET_CHECKS: list[float] = []


def fake_check_budget(worst: float) -> None:
    BUDGET_CHECKS.append(worst)


tr.check_budget = fake_check_budget
tr.append_ledger = LEDGER.append


def tmp() -> Path:
    return Path(tempfile.mkdtemp())


def make_wav(path: Path, rate: int = 24000, channels: int = 1, width: int = 2, seconds: float = 1.5) -> Path:
    frames = int(rate * seconds)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        if width == 2:
            data = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate))) * channels
                            for i in range(frames))
        else:
            data = bytes(frames * channels * width)
        w.writeframes(data)
    return path


class Resp:
    def __init__(self, status: int, body=None, text: str = ""):
        self.status_code = status
        self._body = body
        self.text = text or (json.dumps(body) if body is not None else "")

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def post(self, url, headers=None, files=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "files": files, "timeout": timeout})
        if isinstance(self.resp, Exception):
            raise self.resp
        return self.resp


OK = {"sessionId": "s1", "transcript": "Smoke rising from the peat behind Kalampangan village.",
      "audioDurationMs": 31786, "turns": []}


def expect(exc, fn, *args, **kwargs) -> str:
    try:
        fn(*args, **kwargs)
    except exc as e:
        return str(e)
    raise AssertionError(f"expected {exc.__name__}")


def test_wav_info():
    info = wav_info(make_wav(tmp() / "a.wav", rate=16000, seconds=2.0))
    assert info == {"channels": 1, "sample_rate": 16000, "sample_width": 2, "frames": 32000, "duration_s": 2.0}
    bad = tmp() / "bad.wav"
    bad.write_bytes(b"not a wav file")
    expect(TranscribeError, wav_info, bad)
    expect(TranscribeError, wav_info, tmp() / "missing.wav")


def test_ogg_to_wav_real_ffmpeg():
    assert FFMPEG, "ffmpeg not found: set FFMPEG or add it to PATH"
    d = tmp()
    src_wav = make_wav(d / "src48k.wav", rate=48000, seconds=2.0)
    ogg = d / "note.ogg"
    subprocess.run([FFMPEG, "-nostdin", "-y", "-loglevel", "error", "-i", str(src_wav), "-c:a", "libopus",
                    str(ogg)], check=True, capture_output=True)
    out = ogg_to_wav(ogg, d / "wav", ffmpeg=FFMPEG)
    assert out == d / "wav" / "note.wav"
    info = wav_info(out)
    assert (info["channels"], info["sample_rate"], info["sample_width"]) == (1, 24000, 2), info
    assert 1.9 < info["duration_s"] < 2.1, info
    assert not list((d / "wav").glob("*.part.wav"))


def test_ogg_to_wav_command_and_cache():
    d = tmp()
    calls = []

    def fake_run(cmd, capture_output=None, timeout=None, check=None):
        calls.append((cmd, capture_output, timeout, check))
        make_wav(Path(cmd[-1]))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    src = d / "x.ogg"
    out = ogg_to_wav(src, d / "w", ffmpeg="ffx", run=fake_run)
    part = d / "w" / "x.part.wav"
    assert calls == [(["ffx", "-nostdin", "-y", "-loglevel", "error", "-i", str(src), "-ac", "1", "-ar", "24000",
                       "-c:a", "pcm_s16le", "-map_metadata", "-1", str(part)], True, 120, False)]
    assert out == d / "w" / "x.wav" and out.exists() and not part.exists()
    ogg_to_wav(src, d / "w", ffmpeg="ffx", run=fake_run)
    assert len(calls) == 1, "existing valid wav must be reused"


def test_ogg_to_wav_failures_leave_nothing():
    def failing(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, b"", b"line one\nInvalid data found when processing input\n")

    def missing(cmd, **kw):
        raise FileNotFoundError("ffx")

    def timeout(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 120)

    def wrong_format(cmd, **kw):
        make_wav(Path(cmd[-1]), rate=16000)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    msg = None
    for run in (failing, missing, timeout, wrong_format):
        d = tmp()
        m = expect(TranscribeError, ogg_to_wav, d / "x.ogg", d / "w", ffmpeg="ffx", run=run)
        msg = m if run is failing else msg
        assert not ((d / "w").exists() and any((d / "w").iterdir())), run.__name__
    assert "1" in msg and "Invalid data found" in msg, msg
    d = tmp()
    assert "ffmpeg not found" in expect(TranscribeError, ogg_to_wav, d / "x.ogg", d / "w", run=missing)


def test_transcribe_request_and_ledger():
    LEDGER.clear()
    BUDGET_CHECKS.clear()
    wav = make_wav(tmp() / "note.wav", seconds=31.5)
    s = FakeSession(Resp(200, OK))
    out = transcribe(wav, KEY, keywords=["Kalampangan", "Sebangau"], languages=["English"], session=s)
    assert out == {"transcript": OK["transcript"], "session_id": "s1", "audio_ms": 31786,
                   "cost_usd": round(31 / 3600 * 0.18, 6), "keywords": ["Kalampangan", "Sebangau"]}
    call = s.calls[0]
    assert call["url"] == "https://api.meta.ai/v1/asr/transcribe" and call["timeout"] == 120
    assert call["headers"] == {"Authorization": f"Bearer {KEY}"}
    name, payload, ctype = call["files"]["request"]
    assert name is None and ctype == "application/json"
    assert json.loads(payload) == {"model": "muse-voice-transcribe-1.0", "audioEncoding": "WAV",
                                   "mode": "PUSH_TO_TALK", "keywords": ["Kalampangan", "Sebangau"],
                                   "languageBias": ["English"]}
    assert call["files"]["audio"] == ("note.wav", wav.read_bytes(), "audio/wav")
    assert BUDGET_CHECKS == [math.ceil(31.5) / 3600 * 0.18]
    assert len(LEDGER) == 1
    row = LEDGER[0]
    assert set(row) == {"time", "tag", "model", "cache_key", "cache_hit", "prompt_tokens", "completion_tokens",
                        "reasoning_tokens", "finish_reason", "seconds", "cost_usd", "audio_ms"}
    assert row["tag"] == "transcribe" and row["model"] == "muse-voice-transcribe-1.0"
    assert row["cost_usd"] == round(31 / 3600 * 0.18, 6) and row["audio_ms"] == 31786
    assert row["cache_hit"] is False and row["prompt_tokens"] == 0


def test_optional_fields_omitted():
    s = FakeSession(Resp(200, OK))
    out = transcribe(make_wav(tmp() / "n.wav"), KEY, keywords=[], session=s)
    assert json.loads(s.calls[0]["files"]["request"][1]) == {
        "model": "muse-voice-transcribe-1.0", "audioEncoding": "WAV", "mode": "PUSH_TO_TALK"}
    assert out["keywords"] == []


def test_bad_audio_rejected_before_request():
    LEDGER.clear()
    BUDGET_CHECKS.clear()
    for path in [make_wav(tmp() / "st.wav", channels=2), make_wav(tmp() / "8bit.wav", width=1),
                 make_wav(tmp() / "48k.wav", rate=48000), make_wav(tmp() / "empty.wav", seconds=0)]:
        s = FakeSession(Resp(200, OK))
        expect(TranscribeError, transcribe, path, KEY, session=s)
        assert s.calls == [], path.name
    original = tr.MAX_SECONDS
    tr.MAX_SECONDS = 1
    try:
        s = FakeSession(Resp(200, OK))
        expect(TranscribeError, transcribe, make_wav(tmp() / "long.wav", seconds=2), KEY, session=s)
        assert s.calls == []
    finally:
        tr.MAX_SECONDS = original
    assert BUDGET_CHECKS == [] and LEDGER == []


def test_budget_exceeded_propagates():
    def no_budget(worst):
        raise BudgetExceeded("cap reached")

    tr.check_budget = no_budget
    try:
        s = FakeSession(Resp(200, OK))
        expect(BudgetExceeded, transcribe, make_wav(tmp() / "n.wav"), KEY, session=s)
        assert s.calls == []
    finally:
        tr.check_budget = fake_check_budget


def test_http_and_body_errors():
    LEDGER.clear()
    wav = make_wav(tmp() / "n.wav")
    msg = expect(TranscribeError, transcribe, wav, KEY, session=FakeSession(Resp(429, text="x" * 500)))
    assert "429" in msg and "x" * 201 not in msg
    for resp in [Resp(200, None, text="<html>"), Resp(200, {"transcript": 5, "audioDurationMs": 1}),
                 Resp(200, {"transcript": "hi"})]:
        expect(TranscribeError, transcribe, wav, KEY, session=FakeSession(resp))
    err = None
    try:
        transcribe(wav, KEY, session=FakeSession(requests.ConnectionError(f"failed with key {KEY}")))
    except TranscribeError as e:
        err = e
    assert err is not None and KEY not in str(err) and err.__suppress_context__ is True
    assert LEDGER == []


def test_module_rules():
    src = inspect.getsource(tr)
    assert tr.MODEL == "muse-voice-transcribe-1.0" and tr.SAMPLE_RATE == 24000
    assert tr.MAX_SECONDS == 600 and tr.MAX_BYTES == 32 * 1024 * 1024 and tr.PRICE_PER_HOUR == 0.18
    assert "os.environ" not in src and "getenv" not in src and "print(" not in src
    assert "except Exception" not in src and "except:" not in src and "shell=True" not in src
    # was 170; raised after review of build 1: the extra lines are one-item-per-line formatting,
    # not bloat (see logs/builds/transcribe_review.md)
    assert len(src.splitlines()) <= 210, len(src.splitlines())
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
