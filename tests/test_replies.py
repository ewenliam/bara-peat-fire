"""Tests for the Claude-written WhatsApp replies (src/replies.py). No messages are sent.

Usage:
    python tests/test_replies.py
"""
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import replies  # noqa: E402

SENT: list[tuple] = []


def fresh(env: str = "") -> None:
    d = Path(tempfile.mkdtemp())
    replies.REPLIES = d / "replies.jsonl"
    replies.ENV_FILE = d / ".env"
    replies.ENV_FILE.write_text(env, encoding="utf-8")
    SENT.clear()
    replies.FUSED = lambda: {"wamid.A": {"id": "wamid.A", "reply": "Smoke near Puri matches C5."},
                             "wamid.B": {"id": "wamid.B", "reply": "Where is the fire?"}}
    replies.SEND = lambda to, body: SENT.append((to, body)) or f"wamid.out.{len(SENT)}"


def row(i: str, sender: str = "48111") -> dict:
    return {"id": i, "from": sender}


def res(i: str, status: str = "ok", source: str | None = None) -> dict:
    return {"id": i, "status": status} | ({"source": source} if source else {})


def test_sends_once_per_report():
    fresh()
    out = replies.send_for([row("wamid.A")], [res("wamid.A")])
    assert SENT == [("48111", "Bara: Smoke near Puri matches C5.")] and out[0]["status"] == "sent"
    assert replies.sent()["wamid.A"]["message_id"] == "wamid.out.1"
    replies.send_for([row("wamid.A")], [res("wamid.A")])  # Meta retry: no second message
    assert len(SENT) == 1


def test_skips_statuses_browser_and_unfused():
    fresh()
    replies.send_for([row("wamid.A"), row("wamid.B"), row("wamid.C"), row("web.1")],
                     [res("wamid.A", "error"), res("wamid.B", "skipped"), res("wamid.C"),
                      res("web.1", source="browser")])
    assert SENT == []  # error and skipped rows, a report fusion did not return, and browser uploads


def test_needs_review_still_gets_the_question():
    fresh()
    replies.send_for([row("wamid.B")], [res("wamid.B", "needs_review")])
    assert SENT == [("48111", "Bara: Where is the fire?")]


def test_off_switch():
    fresh("WA_REPLIES=off\n")
    assert replies.send_for([row("wamid.A")], [res("wamid.A")]) == [] and SENT == []


def test_failed_send_is_recorded_not_raised():
    fresh()

    def refuse(to, body):
        raise RuntimeError("HTTP 400: outside the 24 hour window")

    replies.SEND = refuse
    out = replies.send_for([row("wamid.A")], [res("wamid.A")])
    assert out[0]["status"] == "failed" and "24 hour" in out[0]["error"]


def test_send_text_request_shape():
    fresh("WA_PHONE_NUMBER_ID=123\nWA_ACCESS_TOKEN=tok\n")
    calls = []

    class Session:
        def post(self, url, timeout, headers, json):
            calls.append((url, headers, json))
            body = b'{"messages": [{"id": "wamid.X"}]}'
            return SimpleNamespace(status_code=200, content=body, json=lambda: __import__("json").loads(body))

    assert replies.send_text("48111", "hello", session=Session()) == "wamid.X"
    url, headers, payload = calls[0]
    assert url == "https://graph.facebook.com/123/messages" and headers == {"Authorization": "Bearer tok"}
    assert payload == {"messaging_product": "whatsapp", "recipient_type": "individual", "to": "48111",
                       "type": "text", "text": {"body": "hello"}}


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
