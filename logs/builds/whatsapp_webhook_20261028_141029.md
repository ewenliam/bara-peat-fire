# Build build:whatsapp_webhook 20261028_141029

model: muse-spark-1.3
cost_usd: 0.025686
finish_reason: stop

## Prompt

Target file: src/whatsapp_webhook.py

## Spec

# Spec: src/whatsapp_webhook.py (webhook security for the WhatsApp field loop)

Builder: Muse Spark writes this module. Acceptance: `python tests/test_whatsapp_webhook.py`
prints `all passed` and exits 0.

## Context

Meta sends WhatsApp Cloud API events to our HTTPS endpoint. Two checks happen before we
trust anything:

1. GET verification (once, when the webhook is registered). Meta calls the endpoint with
   query parameters `hub.mode` (always `subscribe`), `hub.verify_token` (the string we
   typed in the App Dashboard) and `hub.challenge`. We must answer with the challenge only
   if the token matches ours.
2. POST payload signature (every event). Meta sends header `X-Hub-Signature-256` with
   value `sha256=` followed by the HMAC-SHA256 of the payload, keyed with our App Secret.
   We recompute it over the raw request body bytes exactly as received (never over
   re-serialized JSON) and compare.

## Functions

```python
def verify_signature(body: bytes, header: str | None, app_secret: str) -> bool
```
- `body` must be `bytes` (or `bytearray`); a `str` raises `TypeError`, so callers cannot
  accidentally pass decoded or re-serialized JSON.
- Returns True only if `header` is `"sha256="` followed by exactly 64 hex digits, and those
  digits equal the HMAC-SHA256 hex digest of `body` keyed with `app_secret` encoded as UTF-8.
  The hex digits may be upper or lower case. Leading and trailing whitespace around the
  whole header is ignored. The `sha256=` prefix is case sensitive.
- Compare with `hmac.compare_digest`, never `==`.
- Returns False (never raises) for: `header` None or empty, wrong prefix, wrong length,
  non-hex digits, or an empty `app_secret`.

```python
def verify_subscription(mode: str | None, token: str | None, challenge: str | None,
                        expected_token: str) -> str | None
```
- Returns `challenge` unchanged if `mode == "subscribe"`, `token` equals `expected_token`
  (compared with `hmac.compare_digest`), `challenge` is a non-empty string, and
  `expected_token` is non-empty. Otherwise returns None. Never raises for None inputs.

## Rules
- Standard library only (`hmac`, `hashlib`). No network, no file access, no globals that
  read environment variables; the caller passes the secret and token in.
- Module docstring of one or two lines. Type hints on both functions. PEP 8.
- No logging or printing of the secret, the token, or the computed digest.


## Acceptance tests (tests/test_whatsapp_webhook.py)

```python
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
```

## Feedback on the previous attempt

Review by Claude: hmac.compare_digest raises TypeError on str arguments with non-ASCII characters. hub.verify_token is untrusted input, so verify_subscription must never raise for it. A new test covers this. Compare UTF-8 encoded bytes instead. Also remove the duplicated str check at the top of verify_signature. Keep everything else.

Test output:
```
pass  test_bytearray_and_uppercase_hex_and_whitespace
pass  test_empty_secret_returns_false
pass  test_malformed_headers_return_false
pass  test_reserialized_json_fails
pass  test_str_body_raises_type_error
pass  test_subscription
FAIL  test_subscription_non_ascii_token_does_not_raise: TypeError: comparing strings with non-ASCII characters is not supported
pass  test_tampered_body_and_wrong_secret
pass  test_unicode_body_signed_as_raw_bytes
pass  test_uses_constant_time_compare
pass  test_valid_signature
1 of 11 failed
```


## Reply

```python
"""Verify WhatsApp webhook subscription and signatures."""

import hashlib
import hmac


def verify_signature(body: bytes, header: str | None, app_secret: str) -> bool:
    """Return True when header matches HMAC-SHA256 of raw body."""
    if isinstance(body, str) or not isinstance(body, (bytes, bytearray)):
        raise TypeError("body must be bytes or bytearray")
    if not isinstance(app_secret, str) or not app_secret:
        return False
    if not isinstance(header, str):
        return False
    cleaned = header.strip()
    prefix = "sha256="
    if not cleaned.startswith(prefix):
        return False
    hex_part = cleaned[len(prefix):]
    if len(hex_part) != 64:
        return False
    digits = "0123456789abcdefABCDEF"
    for ch in hex_part:
        if ch not in digits:
            return False
    expected = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.lower(), hex_part.lower())


def verify_subscription(
    mode: str | None,
    token: str | None,
    challenge: str | None,
    expected_token: str,
) -> str | None:
    """Return challenge when subscription verification succeeds."""
    if mode != "subscribe":
        return None
    if not isinstance(challenge, str) or not challenge:
        return None
    if not isinstance(expected_token, str) or not expected_token:
        return None
    if not isinstance(token, str):
        return None
    try:
        token_bytes = token.encode("utf-8")
        expected_bytes = expected_token.encode("utf-8")
    except Exception:
        return None
    if not hmac.compare_digest(token_bytes, expected_bytes):
        return None
    return challenge
```
- Uses only hmac and hashlib with raw bytes HMAC.
- Constant time compare via compare_digest for signature and token.
- Encodes tokens to UTF-8 bytes to avoid non-ASCII compare errors.
- Strict header format checks with outer whitespace strip only.
- No env access, no logging, single str body check raising TypeError.
