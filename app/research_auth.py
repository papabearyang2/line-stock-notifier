"""Minimal password and signed-session helpers for the private dashboard."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time


SESSION_TTL_SECONDS = 12 * 60 * 60


def _is_configured(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def verify_dashboard_password(
    submitted_password: str | None,
    configured_password: str | None,
) -> bool:
    """Compare non-empty passwords without exposing length-dependent comparisons."""
    if not _is_configured(submitted_password) or not _is_configured(configured_password):
        return False
    submitted_digest = hashlib.sha256(submitted_password.encode("utf-8")).digest()
    configured_digest = hashlib.sha256(configured_password.encode("utf-8")).digest()
    return hmac.compare_digest(submitted_digest, configured_digest)


def create_session_token(
    session_secret: str,
    *,
    ttl_seconds: int = SESSION_TTL_SECONDS,
    now: float | None = None,
) -> str:
    """Create a URL-safe, expiring HMAC-SHA256 token containing no password."""
    if not _is_configured(session_secret):
        raise ValueError("session_secret must be configured")
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be a positive integer")

    expires_at = int(time.time() if now is None else now) + ttl_seconds
    body = f"v1.{expires_at}.{secrets.token_urlsafe(18)}"
    signature = hmac.new(
        session_secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
    ).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"{body}.{encoded_signature}"


def verify_session_token(
    token: str | None,
    session_secret: str | None,
    *,
    now: float | None = None,
) -> bool:
    """Return whether a token is authentic and has not reached its expiry time."""
    if not _is_configured(token) or not _is_configured(session_secret):
        return False
    if not token.isascii():
        return False

    parts = token.split(".")
    if len(parts) != 4 or parts[0] != "v1" or not parts[2]:
        return False
    body = ".".join(parts[:3])
    try:
        expires_at = int(parts[1])
        padding = b"=" * (-len(parts[3]) % 4)
        supplied_signature = base64.b64decode(
            parts[3].encode("ascii") + padding,
            altchars=b"-_",
            validate=True,
        )
    except (UnicodeEncodeError, ValueError):
        return False

    expected_signature = hmac.new(
        session_secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
    ).digest()
    if not hmac.compare_digest(supplied_signature, expected_signature):
        return False
    return int(time.time() if now is None else now) < expires_at
