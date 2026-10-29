"""Transcribe WhatsApp voice notes to English text.

Usage example:
    from pathlib import Path
    from transcribe import ogg_to_wav, transcribe

    wav = ogg_to_wav(Path("note.ogg"), Path("wav"))
    result = transcribe(wav, "API_KEY", keywords=["Kalampangan"])
    transcript = result["transcript"]
"""

import json
import math
import subprocess
import time
import wave
from pathlib import Path
from typing import Any, Callable

import requests
from llm import append_ledger, check_budget


class TranscribeError(Exception):
    """Raised for any audio validation, conversion, or API failure."""


MODEL = "muse-voice-transcribe-1.0"
PRICE_PER_HOUR = 0.18
SAMPLE_RATE = 24000
MAX_SECONDS = 600
MAX_BYTES = 32 * 1024 * 1024


def wav_info(path: Path) -> dict:
    """Return channels, sample_rate, sample_width, frames, duration_s."""
    try:
        with wave.open(str(path), "rb") as w:
            channels = w.getnchannels()
            sample_rate = w.getframerate()
            sample_width = w.getsampwidth()
            frames = w.getnframes()
    except (wave.Error, EOFError, OSError):
        raise TranscribeError(f"invalid WAV file: {path.name}") from None
    return {
        "channels": channels,
        "sample_rate": sample_rate,
        "sample_width": sample_width,
        "frames": frames,
        "duration_s": frames / sample_rate if sample_rate else 0.0,
    }


def _is_valid_wav(path: Path) -> bool:
    try:
        info = wav_info(path)
    except TranscribeError:
        return False
    return (
        info["channels"] == 1
        and info["sample_width"] == 2
        and info["sample_rate"] == SAMPLE_RATE
        and info["frames"] > 0
    )


def ogg_to_wav(
    src: Path,
    dest_dir: Path,
    ffmpeg: str = "ffmpeg",
    run: Callable[..., Any] = subprocess.run,
) -> Path:
    """Convert OGG/Opus to mono 24 kHz 16-bit WAV."""
    final = dest_dir / (src.stem + ".wav")
    if final.is_file() and _is_valid_wav(final):
        return final
    dest_dir.mkdir(parents=True, exist_ok=True)
    part = dest_dir / (src.stem + ".part.wav")
    cmd = [
        ffmpeg, "-nostdin", "-y", "-loglevel", "error", "-i", str(src),
        "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le",
        "-map_metadata", "-1", str(part),
    ]
    try:
        proc = run(cmd, capture_output=True, timeout=120, check=False)
    except FileNotFoundError:
        raise TranscribeError("ffmpeg not found") from None
    except subprocess.TimeoutExpired:
        part.unlink(missing_ok=True)
        raise TranscribeError("ffmpeg timed out") from None
    if proc.returncode != 0:
        raw = proc.stderr
        if isinstance(raw, bytes):
            text = raw.decode("utf-8", errors="replace")
        else:
            text = str(raw or "")
        lines = text.splitlines()
        last = lines[-1] if lines else ""
        part.unlink(missing_ok=True)
        raise TranscribeError(f"ffmpeg failed with code {proc.returncode}: {last}")
    if not _is_valid_wav(part):
        part.unlink(missing_ok=True)
        raise TranscribeError("ffmpeg output has wrong format")
    part.replace(final)
    return final


def transcribe(
    wav: Path,
    api_key: str,
    keywords: list[str] | None = None,
    languages: list[str] | None = None,
    session: Any = None,
    base_url: str = "https://api.meta.ai/v1",
    tag: str = "transcribe",
) -> dict:
    """Send a WAV file for transcription and return transcript plus cost."""
    info = wav_info(wav)
    try:
        size = wav.stat().st_size
    except OSError:
        raise TranscribeError("cannot stat WAV file") from None
    if (
        info["channels"] != 1
        or info["sample_width"] != 2
        or info["sample_rate"] not in (16000, 24000)
        or info["frames"] == 0
        or info["duration_s"] > MAX_SECONDS
        or size > MAX_BYTES
    ):
        raise TranscribeError("unsupported WAV format or size") from None
    worst = math.ceil(info["duration_s"]) / 3600 * PRICE_PER_HOUR
    check_budget(worst)
    request_json: dict[str, Any] = {
        "model": MODEL,
        "audioEncoding": "WAV",
        "mode": "PUSH_TO_TALK",
    }
    if keywords:
        request_json["keywords"] = keywords
    if languages:
        request_json["languageBias"] = languages
    if session is None:
        session = requests.Session()
    try:
        wav_bytes = wav.read_bytes()
    except OSError:
        raise TranscribeError("cannot read WAV file") from None
    files = {
        "request": (None, json.dumps(request_json), "application/json"),
        "audio": (wav.name, wav_bytes, "audio/wav"),
    }
    start = time.monotonic()
    try:
        resp = session.post(
            f"{base_url}/asr/transcribe",
            headers={"Authorization": f"Bearer {api_key}"},
            files=files,
            timeout=120,
        )
    except requests.RequestException:
        raise TranscribeError("transcribe request failed") from None
    seconds = round(time.monotonic() - start, 2)
    if resp.status_code != 200:
        raise TranscribeError(
            f"transcribe failed with status {resp.status_code}: {(resp.text or '')[:200]}"
        ) from None
    try:
        data = resp.json()
    except ValueError:
        raise TranscribeError("invalid transcribe response") from None
    if not isinstance(data, dict):
        raise TranscribeError("invalid transcribe response") from None
    transcript = data.get("transcript")
    audio_ms = data.get("audioDurationMs")
    if not isinstance(transcript, str):
        raise TranscribeError("invalid transcribe response") from None
    if not isinstance(audio_ms, int) or isinstance(audio_ms, bool):
        raise TranscribeError("invalid transcribe response") from None
    cost = round((audio_ms // 1000) / 3600 * PRICE_PER_HOUR, 6)
    append_ledger(
        {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "tag": tag,
            "model": MODEL,
            "cache_key": None,
            "cache_hit": False,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "reasoning_tokens": None,
            "finish_reason": None,
            "seconds": seconds,
            "cost_usd": cost,
            "audio_ms": audio_ms,
        }
    )
    return {
        "transcript": transcript,
        "session_id": data.get("sessionId"),
        "audio_ms": audio_ms,
        "cost_usd": cost,
        "keywords": list(keywords) if keywords else [],
    }
