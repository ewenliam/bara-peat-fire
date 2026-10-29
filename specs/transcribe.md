# Spec: src/transcribe.py (WhatsApp voice note to English transcript)

Builder: Muse Spark writes this module. Acceptance: `python tests/test_transcribe.py`
prints `all passed` and exits 0. Tests use a fake HTTP session and a fake ledger; the
ffmpeg tests run the real ffmpeg found through the `FFMPEG` environment variable or `PATH`
(the test file handles that, not the module).

## Context

WhatsApp voice notes arrive as OGG/Opus (checked on a real note: opus, 48 kHz, mono).
Muse Voice Transcribe (`muse-voice-transcribe-1.0`) accepts only a RIFF/WAVE container
with mono 16-bit integer PCM at 16 or 24 kHz, at most 10 minutes and 32 MB per request.

Request: `POST {base_url}/asr/transcribe`, header `Authorization: Bearer <key>`, multipart
with two parts: `request` (JSON, content type `application/json`) and `audio` (the WAV).
Request JSON fields used here: `model`, `audioEncoding` (`"WAV"`), `mode`
(`"PUSH_TO_TALK"`: the clip is one turn), optional `keywords` (list of strings, biases
recognition toward names such as villages), optional `languageBias` (list of language
names such as `["English"]`). Response JSON: `sessionId`, `transcript`, `audioDurationMs`,
`turns` (empty in PUSH_TO_TALK). Price: $0.18 per hour of audio, metered in whole seconds
(partial seconds dropped).

## Interface

```python
class TranscribeError(Exception): ...

MODEL = "muse-voice-transcribe-1.0"
PRICE_PER_HOUR = 0.18
SAMPLE_RATE = 24000
MAX_SECONDS = 600
MAX_BYTES = 32 * 1024 * 1024

def wav_info(path: Path) -> dict
def ogg_to_wav(src: Path, dest_dir: Path, ffmpeg: str = "ffmpeg", run=subprocess.run) -> Path
def transcribe(wav: Path, api_key: str, keywords: list[str] | None = None,
               languages: list[str] | None = None, session=None,
               base_url: str = "https://api.meta.ai/v1", tag: str = "transcribe") -> dict
```

- `wav_info`: open with the standard `wave` module and return
  `{"channels", "sample_rate", "sample_width", "frames", "duration_s"}` (`sample_width` in
  bytes, `duration_s = frames / sample_rate` as float). Any `wave.Error`, `EOFError` or
  `OSError` becomes `TranscribeError`.
- `ogg_to_wav`:
  - Final path `dest_dir / (src.stem + ".wav")`. If it already exists and passes the
    format check below, return it without running ffmpeg.
  - Otherwise create `dest_dir`, convert into `dest_dir / (src.stem + ".part.wav")` by calling
    `run(cmd, capture_output=True, timeout=120, check=False)` with exactly
    `cmd = [ffmpeg, "-nostdin", "-y", "-loglevel", "error", "-i", str(src), "-ac", "1",
    "-ar", "24000", "-c:a", "pcm_s16le", "-map_metadata", "-1", str(part)]`
    (a list, never a shell string).
  - `FileNotFoundError` from `run` becomes `TranscribeError("ffmpeg not found")`;
    `subprocess.TimeoutExpired` becomes `TranscribeError` too. A non-zero return code
    raises `TranscribeError` that includes the return code and the last line of stderr
    (decoded with `errors="replace"`).
  - Format check on the part file: mono, `sample_width == 2`, `sample_rate == SAMPLE_RATE`,
    `frames > 0`. On failure delete the part file and raise `TranscribeError`. On success
    `Path.replace` the part file onto the final path and return the final path. No part file
    is left behind on any failure.
- `transcribe`:
  - `info = wav_info(wav)`. Raise `TranscribeError` (before any request or budget check) if
    not mono, not 16-bit, sample rate not 16000 or 24000, `frames == 0`, duration over
    `MAX_SECONDS`, or file size over `MAX_BYTES`.
  - Worst-case cost `ceil(duration_s) / 3600 * PRICE_PER_HOUR`; call `check_budget(worst)`
    (imported from `llm`, so `BudgetExceeded` propagates unchanged).
  - Request JSON: `{"model": MODEL, "audioEncoding": "WAV", "mode": "PUSH_TO_TALK"}`, plus
    `"keywords": keywords` only if `keywords` is non-empty, plus `"languageBias": languages`
    only if `languages` is non-empty.
  - `session` defaults to `requests.Session()`. Exactly one call:
    `session.post(f"{base_url}/asr/transcribe", headers={"Authorization": f"Bearer {api_key}"},
    files={"request": (None, json.dumps(request_json), "application/json"),
    "audio": (wav.name, wav_bytes, "audio/wav")}, timeout=120)`.
  - `requests.RequestException` becomes `TranscribeError(...) from None`. A non-200 status
    raises `TranscribeError` with the status code and at most the first 200 characters of
    the response text. A body that is not JSON, or lacks a string `transcript` or an int
    `audioDurationMs`, raises `TranscribeError`.
  - Cost `(audioDurationMs // 1000) / 3600 * PRICE_PER_HOUR`. Append one ledger row with
    `append_ledger` (imported from `llm`) holding exactly these keys:
    `time` (`time.strftime("%Y-%m-%dT%H:%M:%S")`), `tag`, `model` (MODEL), `cache_key`
    (None), `cache_hit` (False), `prompt_tokens` (0), `completion_tokens` (0),
    `reasoning_tokens` (None), `finish_reason` (None), `seconds` (request wall time rounded
    to 2 decimals), `cost_usd` (rounded to 6 decimals), `audio_ms` (audioDurationMs).
  - Return `{"transcript", "session_id", "audio_ms", "cost_usd", "keywords"}` where
    `keywords` is the list sent (or `[]`).
- The API key must never appear in any exception message.

## Rules
- Imports: standard library, `requests`, and `check_budget`, `append_ledger` from `llm`
  (imported by name at module level so tests can replace them). No environment variables.
- No `except Exception`, no bare `except`, no `except ...: pass`. No printing. About 150
  lines or fewer, no duplicated logic. Module docstring with a usage example (no `print(`).
  Type hints, PEP 8, plain ASCII, no em dashes.
