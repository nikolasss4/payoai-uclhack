"""Profile pictures for people and organisations.

Images load through <img>, which can't send the session's Bearer token, so each picture is served from a
signed URL. The server hands those URLs only to people allowed to see them. Each URL carries the upload
time, so a new picture gets a new URL and an old one can be cached for good.
"""

import hashlib
import hmac
import os

MAX_BYTES = 2 * 1024 * 1024
KINDS = ("user", "team")


def _secret() -> bytes:
    return os.environ.get("SESSION_SECRET", "local-demo-session-secret").encode()


def signature(kind: str, owner_id: str, version: str) -> str:
    message = f"avatar:{kind}:{owner_id}:{version}".encode()
    return hmac.new(_secret(), message, hashlib.sha256).hexdigest()[:32]


def url(kind: str, owner_id: str | None, updated_at) -> str | None:
    """The picture's URL, or None when there isn't one."""
    if not owner_id or updated_at is None:
        return None
    version = str(int(updated_at.timestamp() * 1000))
    return f"/api/avatars/{kind}/{owner_id}?v={version}&s={signature(kind, owner_id, version)}"


def valid(kind: str, owner_id: str, version: str, sig: str) -> bool:
    return kind in KINDS and hmac.compare_digest(signature(kind, owner_id, version), sig)


def media_type(data: bytes) -> str | None:
    """Reads the type from the file's first bytes, not from its name. SVG isn't accepted, as it can carry script."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def check(data: bytes) -> str:
    """The image's media type, or a ValueError that says what's wrong with it."""
    if not data:
        raise ValueError("That file is empty.")
    if len(data) > MAX_BYTES:
        raise ValueError("Pictures can be up to 2 MB.")
    kind = media_type(data)
    if kind is None:
        raise ValueError("Upload a PNG, JPEG, WebP or GIF picture.")
    return kind


def save(conn, kind: str, owner_id: str, data: bytes, by: str):
    """Stores the picture, replacing any earlier one, and returns its new URL."""
    row = conn.execute(
        """
        INSERT INTO avatars (owner_kind, owner_id, media_type, data, updated_by, updated_at) VALUES (%s, %s, %s, %s, %s, clock_timestamp())
        ON CONFLICT (owner_kind, owner_id) DO UPDATE SET media_type = EXCLUDED.media_type, data = EXCLUDED.data, updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at
        RETURNING updated_at
        """,
        (kind, owner_id, check(data), data, by),
    ).fetchone()
    return url(kind, owner_id, row["updated_at"])


def remove(conn, kind: str, owner_id: str) -> bool:
    return conn.execute("DELETE FROM avatars WHERE owner_kind = %s AND owner_id = %s", (kind, owner_id)).rowcount > 0
