"""Security invariants for Angel One sign-in.

These are not "nice to have" tests. This code path touches other people's
brokerage accounts, so each property below is a hard requirement.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from serving.api.services import auth

REPO = Path(__file__).resolve().parents[2]


def test_no_credential_fields_are_ever_collected():
    """The app must never ask a user for a PIN, password or TOTP secret.

    Scans the auth/portfolio code and the login page for input fields that
    would capture broker credentials.
    """
    suspicious = ["password", "totp", "pin", "mpin"]
    targets = [
        REPO / "serving/api/services/auth.py",
        REPO / "serving/api/services/portfolio.py",
        REPO / "serving/api/routers/auth_router.py",
        REPO / "serving/web/templates/login.html",
    ]
    for path in targets:
        text = path.read_text(encoding="utf-8").lower()
        for token in suspicious:
            # The words may appear in prose explaining what we do NOT collect;
            # what must never appear is a form field or request parameter.
            assert f'name="{token}"' not in text, f"{path.name} collects {token}"
            assert f"'{token}':" not in text, f"{path.name} sends {token}"
            assert f'"{token}":' not in text, f"{path.name} sends {token}"


def test_no_order_placement_code_exists_anywhere():
    """Read-only guarantee: nothing in the repo can trade or move money."""
    forbidden = ["placeorder", "modifyorder", "cancelorder", "/order/v1/",
                 "generatetpin", "fundtransfer"]
    for py in REPO.rglob("*.py"):
        if "__pycache__" in str(py) or "test_auth_security" in py.name:
            continue
        text = py.read_text(encoding="utf-8", errors="ignore").lower()
        for token in forbidden:
            assert token not in text, f"{py} references {token}"


def test_state_nonce_is_single_use():
    state = auth.store.issue_state()
    assert auth.store.consume_state(state) is True
    assert auth.store.consume_state(state) is False       # replay blocked


def test_unknown_state_is_rejected():
    assert auth.store.consume_state("never-issued") is False


def test_session_cookie_is_tamper_evident():
    s = auth.store.create("AUTH", "FEED")
    cookie = auth.sign(s.session_id)
    assert auth.unsign(cookie) == s.session_id

    # Flip the final signature character to something it definitely is not.
    flipped = "0" if cookie[-1] != "0" else "1"
    assert auth.unsign(cookie[:-1] + flipped) is None

    assert auth.unsign("forged.deadbeef") is None
    assert auth.unsign(None) is None
    assert auth.unsign("no-separator") is None
    auth.store.destroy(s.session_id)


def test_broker_token_never_appears_in_the_cookie():
    s = auth.store.create("SUPER_SECRET_AUTH_TOKEN", "FEED")
    cookie = auth.sign(s.session_id)
    assert "SUPER_SECRET_AUTH_TOKEN" not in cookie
    auth.store.destroy(s.session_id)


def test_session_expires_at_midnight_ist_at_the_latest():
    s = auth.Session("sid", "AUTH",
                     created_at=dt.datetime(2026, 7, 21, 23, 30, tzinfo=auth.IST))
    assert s.expires_at == dt.datetime(2026, 7, 22, 0, 0, tzinfo=auth.IST)


def test_expired_sessions_are_not_returned():
    s = auth.store.create("AUTH")
    s.created_at = dt.datetime.now(auth.IST) - dt.timedelta(days=2)
    assert auth.store.get(s.session_id) is None


def test_logout_destroys_the_session():
    s = auth.store.create("AUTH")
    assert auth.store.get(s.session_id) is not None
    auth.store.destroy(s.session_id)
    assert auth.store.get(s.session_id) is None


def test_login_url_requires_a_configured_api_key(monkeypatch):
    monkeypatch.setattr(auth.settings, "smartapi_api_key", "")
    with pytest.raises(RuntimeError):
        auth.login_url("https://x/cb", "state")


def test_login_url_targets_angel_one_and_carries_state(monkeypatch):
    monkeypatch.setattr(auth.settings, "smartapi_api_key", "KEY123")
    url = auth.login_url("https://app.example/auth/callback", "NONCE")
    assert url.startswith("https://smartapi.angelone.in/publisher-login")
    assert "api_key=KEY123" in url and "state=NONCE" in url
