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
