from fastapi.testclient import TestClient

from serving.api.main import app
from serving.api.services import auth

client = TestClient(app, follow_redirects=False)


def test_dashboard_redirects_when_not_signed_in():
    r = client.get("/dashboard")
    assert r.status_code == 302
    assert "/login-page" in r.headers["location"]


def test_holdings_endpoint_requires_a_session():
    assert client.get("/api/me/holdings").status_code == 401


def test_session_endpoint_reports_signed_out():
    assert client.get("/api/session").json() == {"signed_in": False}


def test_callback_without_token_is_rejected():
    assert client.get("/auth/callback?state=x").status_code == 400


def test_callback_with_forged_state_is_rejected():
    r = client.get("/auth/callback?auth_token=STOLEN&state=forged")
    assert r.status_code == 400


def test_full_callback_creates_a_session_and_sets_an_httponly_cookie():
    state = auth.store.issue_state()
    r = client.get(f"/auth/callback?auth_token=TOK&feed_token=FEED&state={state}")
    assert r.status_code == 302
    assert r.headers["location"] == "/dashboard"

    raw = r.headers.get("set-cookie", "")
    assert "httponly" in raw.lower()
    assert "samesite=lax" in raw.lower()
    assert "TOK" not in raw            # broker token must never reach the browser

    signed = client.cookies.get(auth.SESSION_COOKIE)
    assert auth.store.get(auth.unsign(signed)) is not None
    assert client.get("/api/session").json()["signed_in"] is True

    client.get("/logout")
    assert client.get("/api/session").json()["signed_in"] is False


def test_login_page_states_what_is_never_collected():
    body = client.get("/login-page").text.lower()
    assert "totp secret" in body
    assert "never" in body
