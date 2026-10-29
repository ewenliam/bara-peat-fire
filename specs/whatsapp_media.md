# Spec: src/whatsapp_media.py (download WhatsApp voice notes and photos)

Builder: Muse Spark writes this module. Acceptance: `python tests/test_whatsapp_media.py`
prints `all passed` and exits 0. No test touches the network.

## Context

A webhook message of type `audio` or `image` carries `{"id", "mime_type", "sha256", "url"}`.
Download is two GETs, both with header `Authorization: Bearer <token>`:

1. `GET {graph_url}/{media_id}` returns JSON
   `{"messaging_product", "id", "url", "mime_type", "sha256", "file_size"}`.
2. `GET <that url>` returns the file bytes. The URL expires after 5 minutes; a 404 means it
   expired, and the fix is to repeat step 1 once and retry.

Checked on real messages on 2026-10-29 (not stated in Meta's docs):
- The webhook `sha256` is **base64** of the SHA-256 digest (44 characters); the step-1
  `sha256` is **hex** (64 characters). Both describe the same bytes.
- `mime_type` can differ: the webhook said `audio/ogg; codecs=opus`, step 1 said `audio/ogg`.
- `file_size` is an integer.

## Interface

```python
class MediaError(Exception): ...

MAX_BYTES: int  # 16 * 1024 * 1024 (Meta's audio limit; images are capped lower by Meta)
EXTENSIONS: dict[str, str]  # base mime type -> file extension

def verify_sha256(data: bytes, expected: str) -> bool
def media_path(media_dir: Path, media_id: str, mime_type: str) -> Path
def download_media(media_id: str, token: str, media_dir: Path, expected_sha256: str | None = None,
                   session=None, graph_url: str = "https://graph.facebook.com") -> dict
```

- `EXTENSIONS`: `audio/ogg` .ogg, `audio/mpeg` .mp3, `audio/mp4` .m4a, `audio/aac` .aac,
  `audio/amr` .amr, `image/jpeg` .jpg, `image/png` .png.
- `verify_sha256`: True if `expected` is the SHA-256 of `data` as 64 hex digits (either
  case) or as standard base64 of the 32-byte digest. Compare with `hmac.compare_digest`.
  False (never raises) for anything else, including empty or malformed strings.
- `media_path`: `media_dir / f"{media_id}{ext}"`, where `ext` comes from `EXTENSIONS` using
  the mime type before any `;` (stripped, lower case). Raise `MediaError` if `media_id` is
  not a non-empty string of ASCII digits (this blocks path tricks like `../x`) or if the mime
  type is not in `EXTENSIONS`.
- `download_media`:
  - `session` defaults to `requests.Session()`. Every request is exactly
    `session.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)`.
  - Validate `media_id` before any request (same rule as `media_path`).
  - Step 1; a non-200 status raises `MediaError` with the status code.
  - Build the path with `media_path(media_dir, media_id, meta["mime_type"])`. If that file
    already exists and `verify_sha256(existing_bytes, meta["sha256"])` is True, return
    without downloading (`"cached": True`).
  - Step 2. On 404, repeat step 1 once and retry step 2 once with the new URL. Any other
    non-200, or a second failure, raises `MediaError` with the status code.
  - Raise `MediaError` if the body is larger than `MAX_BYTES`, if its length differs from
    `meta["file_size"]`, if `verify_sha256(body, meta["sha256"])` is False, or if
    `expected_sha256` is given and `verify_sha256(body, expected_sha256)` is False.
  - Write atomically: create `media_dir` if needed, write to the same name plus `.part`,
    then `Path.replace` onto the final path. Nothing is left at the final path on failure.
  - Return `{"path": Path, "mime_type": meta["mime_type"], "bytes": int,
    "sha256": hex digest str, "cached": bool}`.
- The token and the media URLs (they carry signed query strings) must never appear in any
  exception message. `requests` exceptions include the URL, so catch only
  `requests.RequestException` around each request and re-raise as
  `MediaError(...) from None`, which also keeps the original out of the traceback.
- Catch nothing else: no `except Exception`, no bare `except`, no `except ...: pass`.
  Filesystem errors propagate unchanged. Keep the module short (about 120 lines or fewer)
  with no duplicated logic and no unused helpers.

## Rules
- Imports: standard library and `requests` only. No environment variables; the caller
  passes the token. No printing or logging, and no `print(` anywhere in the source,
  docstring included.
- Module docstring with a usage example; type hints; PEP 8; plain ASCII, no em dashes.
