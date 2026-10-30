"""Intake pipeline: one stored WhatsApp message in, one field report row out.

Claude-written orchestration (PLAN.md). It calls the Muse Spark-built modules in order and
records what happened; no model decides anything here.

    audio: download_media -> ogg_to_wav -> transcribe (Muse Voice Transcribe) -> extract (Muse Spark)
    text:  extract on the message body
    image: download_media only; photo classification stays in reports.py, which needs a place

Rows are appended to data/whatsapp/reports.jsonl, once per message id. Every processed
message costs Meta API money, so only senders listed in WA_ALLOWED_SENDERS (comma separated,
digits exactly as in the webhook "from" field) are processed; others are recorded as skipped.

Needs in .env: WA_ACCESS_TOKEN, META_API_KEY, META_BASE_URL, WA_ALLOWED_SENDERS; optional FFMPEG.

Usage (process inbox messages that have no report row yet):
    python src/intake.py
"""
import json
import os
import shutil
import threading
import time
from pathlib import Path

import requests
from dotenv import dotenv_values
from openai import OpenAIError

from field_report import extract
from llm import BudgetExceeded
from transcribe import TranscribeError, ogg_to_wav, transcribe
from whatsapp_media import MediaError, download_media

ROOT = Path(__file__).resolve().parents[1]
WA_DIR = ROOT / "data" / "whatsapp"
INBOX = WA_DIR / "inbox.jsonl"
REPORTS = WA_DIR / "reports.jsonl"
ENV_FILE = ROOT / ".env"
MEDIA_DIR = WA_DIR / "media"
WAV_DIR = WA_DIR / "wav"

# Recognition hints for Voice Transcribe: places plus fire words it misheard ("peat" as "pit")
KEYWORDS = ["Bara", "Kalampangan", "Bukit Tunggal", "Sebangau", "Palangka Raya", "peat", "smouldering", "hotspot", "canal"]
LANGUAGES = ["English"]
PLACE_HINTS = ["Kalampangan", "Bukit Tunggal", "Sebangau", "Palangka Raya"]
# Failures that are recorded in the report row; anything else is a bug and propagates
EXPECTED_ERRORS = (MediaError, TranscribeError, BudgetExceeded, OpenAIError,
                   requests.RequestException, KeyError, OSError)
_lock = threading.Lock()


def _env(key: str) -> str:
    # .env first, read per call: the server process may hold stale values that llm.py's
    # load_dotenv copied into os.environ at startup (an expired WhatsApp token did exactly that)
    value = dotenv_values(ENV_FILE).get(key) or os.environ.get(key) or ""
    return value.strip()


def ffmpeg_path() -> str:
    """FFMPEG from .env, else PATH, else the winget link (a running app may predate the install)."""
    winget = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe"
    return _env("FFMPEG") or shutil.which("ffmpeg") or (str(winget) if winget.exists() else "ffmpeg")


def processed_ids() -> set[str]:
    """Ids with a final row; rows that ended in "error" are retried (the latest row wins)."""
    if not REPORTS.exists():
        return set()
    final = {}
    with REPORTS.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                final[row["id"]] = row["status"] != "error"
    return {i for i, done in final.items() if done}


def _append(row: dict) -> None:
    with _lock:
        REPORTS.parent.mkdir(parents=True, exist_ok=True)
        with REPORTS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def process(row: dict) -> dict:
    """Run one inbox row through the pipeline and append the result to reports.jsonl."""
    kind = row.get("type")
    out = {"id": row["id"], "type": kind, "received_at": row.get("received_at"),
           "processed_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "status": "ok", "error": None,
           "media": None, "transcript": None, "transcribe_cost_usd": 0.0, "extraction": None}
    allowed = {s.strip() for s in _env("WA_ALLOWED_SENDERS").split(",") if s.strip()}
    if row.get("from") not in allowed:
        out.update(status="skipped", error="sender not in WA_ALLOWED_SENDERS")
        _append(out)
        return out
    try:
        msg = row["message"]
        if kind in ("audio", "image"):
            body = msg[kind]
            media = download_media(body["id"], _env("WA_ACCESS_TOKEN"), MEDIA_DIR,
                                   expected_sha256=body.get("sha256"))
            out["media"] = {"path": media["path"].relative_to(ROOT).as_posix(), "mime_type": media["mime_type"],
                            "bytes": media["bytes"], "caption": body.get("caption")}
        if kind == "audio":
            wav = ogg_to_wav(media["path"], WAV_DIR, ffmpeg=ffmpeg_path())
            heard = transcribe(wav, _env("META_API_KEY"), keywords=KEYWORDS, languages=LANGUAGES,
                               base_url=_env("META_BASE_URL") or "https://api.meta.ai/v1")
            out.update(transcript=heard["transcript"], transcribe_cost_usd=heard["cost_usd"])
        elif kind == "text":
            out["transcript"] = msg["text"]["body"]
        elif kind != "image":
            out.update(status="unsupported", error=f"message type {kind!r} is not handled")
        if out["transcript"]:
            extraction = extract(out["transcript"], place_hints=PLACE_HINTS)
            out["extraction"] = {k: v for k, v in extraction.items() if k != "llm_calls"}
            if not extraction["grounded"]:
                out["status"] = "needs_review"  # no report, or a claim not backed by the words said
    except EXPECTED_ERRORS as e:
        out.update(status="error", error=f"{type(e).__name__}: {e}")
    _append(out)
    return out


def process_rows(rows: list[dict]) -> list[dict]:
    """Process rows that have no report yet; called by the webhook after it stores messages."""
    done = processed_ids()
    return [process(row) for row in rows if row["id"] not in done]


def process_pending() -> list[dict]:
    if not INBOX.exists():
        return []
    rows = [json.loads(line) for line in INBOX.read_text(encoding="utf-8").splitlines() if line.strip()]
    return process_rows(rows)


if __name__ == "__main__":
    for r in process_pending():
        ext = r["extraction"] or {}
        print(r["id"][-12:], r["type"], r["status"], r["error"] or "",
              "| grounded:", ext.get("grounded"), "| cost:", round(r["transcribe_cost_usd"] + (ext.get("cost_usd") or 0), 6))
