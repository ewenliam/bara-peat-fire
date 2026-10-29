"""Tests for the /webhook routes in src/whatsapp_routes.py, against a real uvicorn server.

Uses test secrets from the environment (never the real .env values) and a temporary inbox.

Usage:
    python tests/test_whatsapp_routes.py
"""
import hashlib
import hmac
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
TEST_ENV = Path(tempfile.mkdtemp()) / ".env"  # never the real .env
TEST_ENV.write_text("WA_APP_SECRET=test-app-secret\nWA_VERIFY_TOKEN=test-verify-token-123\n", encoding="utf-8")

import whatsapp_routes  # noqa: E402

whatsapp_routes.ENV_FILE = TEST_ENV

whatsapp_routes.INBOX = Path(tempfile.mkdtemp()) / "inbox.jsonl"
PROCESSED: list[list[dict]] = []
whatsapp_routes.PROCESS = PROCESSED.append  # never the real pipeline: no API calls in tests
app = FastAPI()
app.include_router(whatsapp_routes.router)

with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]
URL = f"http://127.0.0.1:{PORT}/webhook"


def payload(msg_id: str = "wamid.TEST1", field: str = "messages") -> bytes:
    msg = {"from": "48000000000", "id": msg_id, "timestamp": "1761650000", "type": "text",
           "text": {"body": "Api di Kalampangan, asap tebal"}}
    return json.dumps({"object": "whatsapp_business_account",
                       "entry": [{"id": "0", "changes": [{"field": field, "value": {
                           "messaging_product": "whatsapp", "messages": [msg]}}]}]}).encode()


def post(body: bytes, secret: str = "test-app-secret", header: str | None = None):
    sig = header or "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return requests.post(URL, data=body, headers={"X-Hub-Signature-256": sig,
                                                  "Content-Type": "application/json"}, timeout=5)


def inbox_rows() -> list[dict]:
    if not whatsapp_routes.INBOX.exists():
        return []
    return [json.loads(line) for line in whatsapp_routes.INBOX.read_text(encoding="utf-8").splitlines()]


def test_1_get_challenge():
    ok = requests.get(URL, params={"hub.mode": "subscribe", "hub.verify_token": "test-verify-token-123",
                                   "hub.challenge": "1158201444"}, timeout=5)
    assert ok.status_code == 200 and ok.text == "1158201444", (ok.status_code, ok.text)
    bad = requests.get(URL, params={"hub.mode": "subscribe", "hub.verify_token": "wrong",
                                    "hub.challenge": "1158201444"}, timeout=5)
    assert bad.status_code == 403
    assert requests.get(URL, timeout=5).status_code == 403


def test_2_post_stores_message_once():
    r = post(payload())
    assert r.status_code == 200 and r.json() == {"stored": 1}, (r.status_code, r.text)
    r = post(payload())  # Meta retry of the same message id
    assert r.status_code == 200 and r.json() == {"stored": 0}
    rows = inbox_rows()
    assert len(rows) == 1 and rows[0]["id"] == "wamid.TEST1" and rows[0]["type"] == "text"
    for _ in range(50):  # background task runs after the response
        if PROCESSED:
            break
        time.sleep(0.05)
    assert [[r["id"] for r in batch] for batch in PROCESSED] == [["wamid.TEST1"]], PROCESSED
    assert rows[0]["message"]["text"]["body"] == "Api di Kalampangan, asap tebal"


def test_3_bad_signatures_rejected_and_not_stored():
    body = payload("wamid.BAD")
    assert post(body, secret="other").status_code == 401
    assert post(body, header="sha256=" + "0" * 64).status_code == 401
    assert requests.post(URL, data=body, timeout=5).status_code == 401  # no header
    assert all(r["id"] != "wamid.BAD" for r in inbox_rows())


def test_4_non_message_fields_and_bad_json():
    r = post(payload("wamid.OTHER", field="account_update"))
    assert r.status_code == 200 and r.json() == {"stored": 0}
    assert post(b"not json").status_code == 400
    assert post(b"[1, 2]").status_code == 400


if __name__ == "__main__":
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"pass  {name}")
        except Exception as e:  # report every failure, not just the first
            failed += 1
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    server.should_exit = True
    print("all passed" if not failed else f"{failed} of {len(tests)} failed")
    sys.exit(1 if failed else 0)
