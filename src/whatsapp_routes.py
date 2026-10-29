"""WhatsApp Cloud API webhook: GET verification, then signed POST events into an inbox file.

Included by server.py. Webhook handling stays deterministic and Claude-written (PLAN.md);
the signature check itself is the Muse Spark-built src/whatsapp_webhook.py. Each new
message is appended once (by message id) to data/whatsapp/inbox.jsonl with its raw
message object, then the new rows go to src/intake.py in a background task, so Meta gets
its 200 at once. data/ is gitignored, which matters here because rows hold sender phone
numbers.

Needs WA_APP_SECRET and WA_VERIFY_TOKEN in .env (or the environment).
"""
import json
import os
import threading
import time
from pathlib import Path

from dotenv import dotenv_values
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import PlainTextResponse

from intake import process_rows
from whatsapp_webhook import verify_signature, verify_subscription

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "data" / "whatsapp" / "inbox.jsonl"
ENV_FILE = ROOT / ".env"
_lock = threading.Lock()
PROCESS = process_rows  # replaced in tests so they make no API calls

router = APIRouter()


def _env(key: str) -> str:
    # .env first, read per request, so a changed .env applies without a restart (the
    # process environment can hold stale copies loaded at startup); environment is the fallback
    value = dotenv_values(ENV_FILE).get(key) or os.environ.get(key) or ""
    return value.strip()


def _seen_ids() -> set[str]:
    if not INBOX.exists():
        return set()
    with INBOX.open(encoding="utf-8") as f:
        return {json.loads(line)["id"] for line in f if line.strip()}


def store_messages(payload: dict) -> list[dict]:
    """Append messages not stored before and return those new rows. Statuses are ignored."""
    rows = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            if change.get("field") != "messages":
                continue
            for msg in (change.get("value") or {}).get("messages") or []:
                if msg.get("id"):
                    rows.append({"received_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                 "id": msg["id"], "from": msg.get("from"),
                                 "type": msg.get("type"), "timestamp": msg.get("timestamp"),
                                 "message": msg})
    with _lock:
        seen = _seen_ids()
        new = []
        for row in rows:
            if row["id"] not in seen:  # Meta retries deliveries, so the same id can repeat
                seen.add(row["id"])
                new.append(row)
        if new:
            INBOX.parent.mkdir(parents=True, exist_ok=True)
            with INBOX.open("a", encoding="utf-8") as f:
                for row in new:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return new


@router.get("/webhook")
def subscribe(request: Request) -> PlainTextResponse:
    q = request.query_params
    challenge = verify_subscription(q.get("hub.mode"), q.get("hub.verify_token"),
                                    q.get("hub.challenge"), _env("WA_VERIFY_TOKEN"))
    if challenge is None:
        raise HTTPException(403, "verification failed")
    return PlainTextResponse(challenge)


@router.post("/webhook")
async def receive(request: Request, background_tasks: BackgroundTasks) -> dict:
    body = await request.body()  # raw bytes: the signature covers them exactly as sent
    if not verify_signature(body, request.headers.get("x-hub-signature-256"), _env("WA_APP_SECRET")):
        raise HTTPException(401, "bad signature")
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(400, "body is not JSON")
    if not isinstance(payload, dict):
        raise HTTPException(400, "body is not a JSON object")
    new = store_messages(payload)
    if new:
        background_tasks.add_task(PROCESS, new)
    return {"stored": len(new)}
