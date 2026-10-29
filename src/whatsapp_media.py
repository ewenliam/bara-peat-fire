"""Download WhatsApp voice notes and photos.

Usage:
    from pathlib import Path
    from whatsapp_media import download_media

    result = download_media("2708226019592949", token, Path("media"))
    path = result["path"]
    mime = result["mime_type"]
"""

import base64
import hashlib
import hmac
from pathlib import Path

import requests


class MediaError(Exception):
    """Raised when media metadata or download fails."""


MAX_BYTES = 16 * 1024 * 1024

EXTENSIONS = {
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
    "audio/amr": ".amr",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}


def verify_sha256(data: bytes, expected: str | None) -> bool:
    """Return True if expected matches the SHA-256 of data."""
    if not isinstance(data, (bytes, bytearray)):
        return False
    if not isinstance(expected, str) or not expected:
        return False
    digest = hashlib.sha256(data).digest()
    exp = expected.encode("utf-8")
    hex_exp = hashlib.sha256(data).hexdigest().encode("ascii")
    if hmac.compare_digest(exp.lower(), hex_exp):
        return True
    b64 = base64.b64encode(digest)
    return hmac.compare_digest(exp, b64)


def _check_media_id(media_id: str) -> None:
    if not isinstance(media_id, str):
        raise MediaError("invalid media id")
    if not media_id.isascii() or not media_id.isdigit():
        raise MediaError("invalid media id")


def media_path(media_dir: Path, media_id: str, mime_type: str) -> Path:
    """Return the local path for a media id and mime type."""
    _check_media_id(media_id)
    if not isinstance(mime_type, str):
        raise MediaError("unsupported mime type")
    base = mime_type.split(";")[0].strip().lower()
    if base not in EXTENSIONS:
        raise MediaError("unsupported mime type")
    return media_dir / f"{media_id}{EXTENSIONS[base]}"


def _get(session, url: str, token: str):
    try:
        headers = {"Authorization": f"Bearer {token}"}
        return session.get(url, headers=headers, timeout=30)
    except requests.RequestException:
        raise MediaError("request failed") from None


def _fetch_meta(session, url: str, token: str) -> dict:
    resp = _get(session, url, token)
    if resp.status_code != 200:
        raise MediaError(f"metadata failed {resp.status_code}")
    return resp.json()


def download_media(
    media_id: str,
    token: str,
    media_dir: Path,
    expected_sha256: str | None = None,
    session=None,
    graph_url: str = "https://graph.facebook.com",
) -> dict:
    """Download media, verify it, and store it atomically."""
    _check_media_id(media_id)
    if session is None:
        session = requests.Session()
    meta_url = f"{graph_url}/{media_id}"
    meta = _fetch_meta(session, meta_url, token)
    path = media_path(media_dir, media_id, meta["mime_type"])
    if path.is_file():
        existing = path.read_bytes()
        if verify_sha256(existing, meta["sha256"]):
            hex_d = hashlib.sha256(existing).hexdigest()
            return {
                "path": path,
                "mime_type": meta["mime_type"],
                "bytes": len(existing),
                "sha256": hex_d,
                "cached": True,
            }
    resp = _get(session, meta["url"], token)
    if resp.status_code == 404:
        meta = _fetch_meta(session, meta_url, token)
        path = media_path(media_dir, media_id, meta["mime_type"])
        resp = _get(session, meta["url"], token)
    if resp.status_code != 200:
        raise MediaError(f"download failed {resp.status_code}")
    body = resp.content
    if len(body) > MAX_BYTES:
        raise MediaError("file too large")
    if len(body) != meta["file_size"]:
        raise MediaError("size mismatch")
    if not verify_sha256(body, meta["sha256"]):
        raise MediaError("hash mismatch")
    if expected_sha256 is not None:
        if not verify_sha256(body, expected_sha256):
            raise MediaError("hash mismatch")
    media_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(body)
    tmp.replace(path)
    return {
        "path": path,
        "mime_type": meta["mime_type"],
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "cached": False,
    }
