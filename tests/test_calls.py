"""Tests for the live Meta model calls panel and the public-deployment lock. No API calls.

Usage:
    python tests/test_calls.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402

import llm  # noqa: E402
import server  # noqa: E402

NOW = time.mktime((2026, 11, 4, 12, 0, 0, 0, 0, -1))


def row(seconds_ago: float, tag: str, **extra) -> str:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW - seconds_ago))
    return json.dumps({"time": stamp, "tag": tag, "model": "muse-spark-1.3", "cache_key": "secret-looking",
                       "cache_hit": False, "seconds": 1.5, "cost_usd": 0.01, **extra})


def ledger(*rows: str) -> None:
    llm.LEDGER = Path(tempfile.mkdtemp()) / "meta_usage.jsonl"
    llm.LEDGER.write_text("\n".join(rows) + "\n\n", encoding="utf-8")


def test_newest_first_and_limited():
    ledger(*[row(100 - i, f"call {i}") for i in range(10)])
    got = llm.recent_calls(limit=3, now=NOW)
    assert [c["tag"] for c in got] == ["call 9", "call 8", "call 7"]


def test_old_calls_are_left_out():
    ledger(row(5000, "old"), row(10, "new"))
    assert [c["tag"] for c in llm.recent_calls(within_s=900, now=NOW)] == ["new"]


def test_no_cost_or_cache_key_reaches_the_page():
    ledger(row(10, "x"))
    assert set(llm.recent_calls(now=NOW)[0]) == {"time", "model", "tag", "seconds", "cache_hit"}


def test_rows_without_a_time_are_skipped():
    # The real ledger has early hand-logged rows with only these fields.
    ledger(json.dumps({"tag": "early", "model": "muse-spark-1.3", "cache_hit": False, "cost_usd": 0.1}), row(10, "new"))
    assert [c["tag"] for c in llm.recent_calls(now=NOW)] == ["new"]


def test_missing_ledger_is_empty():
    llm.LEDGER = Path(tempfile.mkdtemp()) / "none.jsonl"
    assert llm.recent_calls(now=NOW) == []


def test_public_deployment_shows_no_calls_and_refuses_paid_actions():
    ledger(row(0, "x"))
    client = TestClient(server.app, client=("127.0.0.1", 5000))  # a proxy may look like localhost
    os.environ["VERCEL"] = "1"
    try:
        assert client.get("/api/calls").json() == {"calls": []}
        assert client.get("/api/meta").json()["public"] is True
        assert client.post("/api/chat", json={"date": "2026-11-01", "question": "x"}).status_code == 403
    finally:
        del os.environ["VERCEL"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
