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
