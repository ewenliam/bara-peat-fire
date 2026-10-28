"""Acceptance tests for src/whatsapp_webhook.py (written by Claude, see specs/whatsapp_webhook.md).

Usage:
    python tests/test_whatsapp_webhook.py
"""
import hashlib
import hmac
import inspect
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from whatsapp_webhook import verify_signature, verify_subscription  # noqa: E402

SECRET = "test-app-secret"
BODY = json.dumps({"object": "whatsapp_business_account",
                   "entry": [{"changes": [{"value": {"messages": [{"type": "audio"}]}}]}]},
                  separators=(",", ":")).encode()


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_valid_signature():
    assert verify_signature(BODY, sign(BODY), SECRET) is True


def test_bytearray_and_uppercase_hex_and_whitespace():
    header = sign(BODY)
    assert verify_signature(bytearray(BODY), header, SECRET) is True
    assert verify_signature(BODY, "sha256=" + header[7:].upper(), SECRET) is True
    assert verify_signature(BODY, f"  {header}\n", SECRET) is True


def test_unicode_body_signed_as_raw_bytes():
    body = '{"text":"Api di Kalampangan, asap tebal é"}'.encode("utf-8")
    assert verify_signature(body, sign(body), SECRET) is True


def test_reserialized_json_fails():
    pretty = json.dumps(json.loads(BODY), indent=2).encode()
    assert verify_signature(pretty, sign(BODY), SECRET) is False


def test_tampered_body_and_wrong_secret():
    assert verify_signature(BODY + b" ", sign(BODY), SECRET) is False
    assert verify_signature(BODY, sign(BODY, "other-secret"), SECRET) is False


def test_malformed_headers_return_false():
    digest = sign(BODY)[7:]
    for header in [None, "", "sha256=", digest, "sha1=" + digest, "SHA256=" + digest,
                   "sha256=" + digest[:-1], "sha256=" + digest + "0",
                   "sha256=" + "z" * 64, "sha256= " + digest[1:]]:
        assert verify_signature(BODY, header, SECRET) is False, header


def test_empty_secret_returns_false():
    assert verify_signature(BODY, sign(BODY, ""), "") is False


def test_str_body_raises_type_error():
    try:
        verify_signature(BODY.decode(), sign(BODY), SECRET)
    except TypeError:
        return
    raise AssertionError("str body must raise TypeError")


def test_uses_constant_time_compare():
    import whatsapp_webhook
    source = inspect.getsource(whatsapp_webhook)
    assert source.count("compare_digest") >= 2
    assert "os.environ" not in source and "getenv" not in source


def test_subscription():
    assert verify_subscription("subscribe", "tok", "1158201444", "tok") == "1158201444"
    assert verify_subscription("subscribe", "bad", "1158201444", "tok") is None
    assert verify_subscription("unsubscribe", "tok", "1158201444", "tok") is None
    assert verify_subscription("subscribe", "tok", "", "tok") is None
    assert verify_subscription("subscribe", "", "1158201444", "") is None
    assert verify_subscription(None, None, None, "tok") is None


def test_subscription_non_ascii_token_does_not_raise():
    # hub.verify_token comes from the internet; compare_digest raises on non-ASCII str
    assert verify_subscription("subscribe", "té", "1158201444", "tok") is None
    assert verify_subscription("subscribe", "té", "1158201444", "té") == "1158201444"


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
