import re

import pytest

from app.research_auth import (
    create_session_token,
    verify_dashboard_password,
    verify_session_token,
)


def test_valid_password_and_session_token():
    assert verify_dashboard_password("correct horse", "correct horse")

    token = create_session_token("independent session secret", ttl_seconds=60, now=1_000)

    assert re.fullmatch(r"[A-Za-z0-9_.-]+", token)
    assert "correct horse" not in token
    assert verify_session_token(token, "independent session secret", now=1_059)
    assert not verify_session_token(token, "wrong session secret", now=1_059)


def test_wrong_password_is_rejected():
    assert not verify_dashboard_password("wrong", "correct")


def test_tampered_session_token_is_rejected():
    token = create_session_token("session secret", now=1_000)
    body, signature = token.rsplit(".", 1)
    replacement = "A" if signature[0] != "A" else "B"

    assert not verify_session_token(f"{body}.{replacement}{signature[1:]}", "session secret", now=1_001)
    assert not verify_session_token(f"{body}.不是簽章", "session secret", now=1_001)


def test_expired_session_token_is_rejected():
    token = create_session_token("session secret", ttl_seconds=60, now=1_000)

    assert verify_session_token(token, "session secret", now=1_059)
    assert not verify_session_token(token, "session secret", now=1_060)


def test_unconfigured_password_or_secret_is_rejected():
    assert not verify_dashboard_password("", "")
    assert not verify_dashboard_password("submitted", "")
    assert not verify_dashboard_password("", "configured")
    assert not verify_session_token("token", "")
    assert not verify_session_token("", "configured")

    with pytest.raises(ValueError, match="session_secret"):
        create_session_token("")
