"""Login, callback, session and personalised-portfolio routes."""
from __future__ import annotations

from fastapi import APIRouter, Cookie, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from fundxray_core.utils.logging import get_logger

from ..services import auth, portfolio

log = get_logger(__name__)
router = APIRouter()

COOKIE_MAX_AGE = 8 * 60 * 60


def _require_session(cookie_value: str | None) -> auth.Session:
    sid = auth.unsign(cookie_value)
    session = auth.store.get(sid)
    if session is None:
        raise HTTPException(401, "Not signed in, or your Angel One session expired. "
                                 "Angel One sessions end at midnight IST.")
    return session


@router.get("/login")
def login(request: Request, api_key: str = ""):
    """Redirect to Angel One's own login page.

    The user authenticates on angelone.in. This server never sees their client
    code, PIN or TOTP.

    `api_key` is REQUIRED and must be the visitor's own SmartAPI app key,
    supplied via the form on the sign-in page. This server has no default —
    SMARTAPI_API_KEY is not consulted here at all.
    """
    key = api_key.strip()
    if not key:
        raise HTTPException(
            400,
            "Enter your own SmartAPI app key to sign in — register one free "
            "at https://smartapi.angelone.in/, then paste it into the form."
        )
    redirect_url = str(request.url_for("auth_callback"))
    # The chosen key travels with the CSRF nonce, not a cookie, so it can't be
    # tampered with client-side between here and the callback.
    state = auth.store.issue_state(api_key=key)
    return RedirectResponse(auth.login_url(redirect_url, state, api_key=key), status_code=302)


@router.get("/auth/callback", name="auth_callback")
def auth_callback(request: Request, response: Response, auth_token: str = "",
                  feed_token: str = "", refresh_token: str = "", state: str = ""):
    """Angel One redirects here with short-lived tokens."""
    if not auth_token:
        raise HTTPException(400, "Angel One did not return an auth token.")

    # Single-use nonce: blocks CSRF and replayed callback URLs. Also recovers
    # the api_key this particular login was started with.
    used_api_key = auth.store.consume_state(state)
    if not used_api_key:
        raise HTTPException(400, "Invalid or expired login state. Please start again.")

    session = auth.store.create(auth_token, feed_token, refresh_token, api_key=used_api_key)
    redirect = RedirectResponse("/dashboard", status_code=302)
    # Secure is set whenever we are actually on HTTPS. Deriving it from the
    # request scheme keeps production strict without breaking local http.
    redirect.set_cookie(
        auth.SESSION_COOKIE, auth.sign(session.session_id),
        max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax",
        secure=request.url.scheme == "https",
    )
    return redirect


@router.post("/logout")
@router.get("/logout")
def logout(fx_session: str | None = Cookie(default=None)):
    auth.store.destroy(auth.unsign(fx_session))
    r = RedirectResponse("/", status_code=302)
    r.delete_cookie(auth.SESSION_COOKIE)
    return r


@router.get("/api/session")
def session_info(fx_session: str | None = Cookie(default=None)):
    sid = auth.unsign(fx_session)
    s = auth.store.get(sid)
    if s is None:
        return {"signed_in": False}
    return {"signed_in": True, "expires_at": s.expires_at.isoformat(),
            "client_code": s.client_code or None}


@router.get("/api/me/holdings")
def my_holdings(fx_session: str | None = Cookie(default=None)):
    """The signed-in user's own demat holdings, analysed."""
    session = _require_session(fx_session)
    try:
        holdings = portfolio.fetch_holdings(session.auth_token, session.api_key)
        totals = portfolio.fetch_totals(session.auth_token, session.api_key)
    except Exception as e:
        log.warning("SmartAPI holdings fetch failed: %s", e)
        raise HTTPException(502, f"Could not reach Angel One: {e}")

    result = portfolio.analyse(holdings)
    result["totals"] = totals
    result["as_of"] = session.created_at.isoformat()
    return result


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(fx_session: str | None = Cookie(default=None)):
    from pathlib import Path
    if auth.store.get(auth.unsign(fx_session)) is None:
        return RedirectResponse("/login-page", status_code=302)
    f = Path(__file__).resolve().parents[2] / "web" / "templates" / "dashboard.html"
    return f.read_text(encoding="utf-8")


@router.get("/login-page", response_class=HTMLResponse)
def login_page():
    from pathlib import Path
    f = Path(__file__).resolve().parents[2] / "web" / "templates" / "login.html"
    return f.read_text(encoding="utf-8")
