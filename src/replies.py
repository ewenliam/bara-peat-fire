"""WhatsApp replies: Bara answers each field report from the test number.

Claude-written. Switched on by the user on 2026-11-02 for their own number (the only allowlisted
sender). After intake.py stores a WhatsApp report, the deterministic fusion rules (fusion.py) run on the
latest live day with all recent reports, and that report's reply (fusion.reply_text, the text the app's
phone frame shows) is sent back to the sender through the Cloud API. One reply per message id, recorded
in data/whatsapp/replies.jsonl, so Meta's retries never send twice. Set WA_REPLIES=off in .env to stop
sending; the app then shows the replies as drafts. Browser recordings get no reply (no phone to answer).
"""
import json
import os
import time
from pathlib import Path

import requests
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
REPLIES = ROOT / "data" / "whatsapp" / "replies.jsonl"
LIVE = ROOT / "data" / "processed" / "live_2026.json"
GRAPH = "https://graph.facebook.com"
ENV_FILE = ROOT / ".env"
REPLY_STATUSES = ("ok", "needs_review")  # skipped, unsupported and error rows get no reply


def _env(key: str) -> str:
    return (dotenv_values(ENV_FILE).get(key) or os.environ.get(key) or "").strip()


def enabled() -> bool:
    return _env("WA_REPLIES").lower() != "off"


def sent() -> dict[str, dict]:
    """Latest reply record per report id (sent or failed)."""
    if not REPLIES.exists():
        return {}
    rows = [json.loads(x) for x in REPLIES.read_text(encoding="utf-8").splitlines() if x.strip()]
    return {r["id"]: r for r in rows}


def send_text(to: str, body: str, session=None) -> str:
    """Send one text message; return its WhatsApp message id. Raises RuntimeError on any refusal."""
    s = session or requests
    r = s.post(f"{GRAPH}/{_env('WA_PHONE_NUMBER_ID')}/messages", timeout=30,
               headers={"Authorization": f"Bearer {_env('WA_ACCESS_TOKEN')}"},
               json={"messaging_product": "whatsapp", "recipient_type": "individual", "to": to,
                     "type": "text", "text": {"body": body[:4096]}})
    data = r.json() if r.content else {}
    if r.status_code != 200 or not data.get("messages"):
        raise RuntimeError(f"HTTP {r.status_code}: {(data.get('error') or {}).get('message', 'no message id')}")
    return data["messages"][0]["id"]


def _fused_reports() -> dict[str, dict]:
    """Fusion on the latest live day with every recent report: report id -> fused report (with reply)."""
    from fusion import fuse_day, recent_reports
    from tools import FireTools
    live = json.loads(LIVE.read_text(encoding="utf-8"))
    tools = FireTools(live["live_day"])
    out = fuse_day(tools.table(), tools._routes(), recent_reports())
    return {r["id"]: r for r in out["reports"]}


FUSED = _fused_reports  # replaced in tests
SEND = send_text        # replaced in tests


def send_for(rows: list[dict], results: list[dict]) -> list[dict]:
    """Reply to each newly processed WhatsApp report; return the reply records written."""
    if not enabled():
        return []
    done = sent()
    todo = [(row, res) for row, res in zip(rows, results)
            if res["status"] in REPLY_STATUSES and row.get("from") and res.get("source") != "browser"
            and res["id"] not in done]
    if not todo:
        return []
    fused = FUSED()
    records = []
    for row, res in todo:
        rep = fused.get(res["id"])
        if rep is None:  # too old for the recent window, or not fused: nothing grounded to say
            continue
        body = f"Bara: {rep['reply']}"
        rec = {"id": res["id"], "sent_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "body": body}
        try:
            rec |= {"status": "sent", "message_id": SEND(row["from"], body)}
        except (RuntimeError, requests.RequestException) as e:
            rec |= {"status": "failed", "error": str(e)}
        records.append(rec)
    if records:
        REPLIES.parent.mkdir(parents=True, exist_ok=True)
        with REPLIES.open("a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return records
