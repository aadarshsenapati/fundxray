"""Angel One SmartAPI Publisher Login.

SECURITY POSTURE — read before changing anything here.

FundXRay never asks a user for their Angel One password or TOTP secret, and
never stores either. Doing so would be indefensible:

  * the TOTP secret is the *seed* for their second factor. Holding it means
    being able to generate valid 2FA codes forever, not just once.
  * client code + PIN + TOTP seed is complete account access — including the
    ability to place orders and move money.
  * one breach of this repo's database would hand attackers full trading
    control of every user's brokerage account.

Instead we use Angel One's Publisher Login flow: the user is redirected to
Angel One's own domain, authenticates there, and Angel One redirects back with
short-lived tokens. Credentials never touch this server.

    /login  ->  https://smartapi.angelone.in/publisher-login?api_key=..&redirect_url=..&state=..
            ->  Angel One authenticates the user
            ->  GET /auth/callback?auth_token=..&feed_token=..&state=..

BRING-YOUR-OWN-KEY — REQUIRED, NOT OPTIONAL
--------------------------------------------
This server does not run its own registered SmartAPI app. `SMARTAPI_API_KEY`
is not read anywhere in this login flow. Every visitor must register their own
free app at https://smartapi.angelone.in/ and paste that key into the sign-in
form. `api_key` above identifies that visitor's own registered app, not this
server. Whichever key was used for a given login is remembered for the
lifetime of that session (`Session.api_key`), and every subsequent SmartAPI
call for that user re-uses it (see serving/api/services/portfolio.py). The key
travels through the CSRF state nonce, never through a cookie or anything
client-writable after the fact, and is discarded when the session ends.

Additional guarantees:
  * tokens live server-side only, keyed by an opaque signed session id; the
    browser never receives a broker token
  * the session cookie is HttpOnly + SameSite=Lax (and Secure in production)
  * `state` is a single-use CSRF nonce, verified on callback
  * sessions expire at Angel One's own limit — midnight IST — or earlier
  * FundXRay only ever calls READ endpoints. There is no code path in this
    repository that can place, modify or cancel an order.
"""
from __future__ import annotations

import datetime as dt
import hmac
import os
import secrets
import threading
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Optional
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fundxray_core.utils.logging import get_logger

log = get_logger(__name__)

PUBLISHER_LOGIN_URL = "https://smartapi.angelone.in/publisher-login"
IST = ZoneInfo("Asia/Kolkata")
SESSION_COOKIE = "fx_session"

# Signing key for the session cookie. Set FUNDXRAY_SECRET in production;
# a random per-process key simply invalidates sessions on restart.
_SECRET = os.getenv("FUNDXRAY_SECRET", secrets.token_hex(32)).encode()


@dataclass
class Session:
    session_id: str
    auth_token: str
    feed_token: str = ""
    refresh_token: str = ""
    client_code: str = ""
    api_key: str = ""          # the SmartAPI app key this login actually used
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(IST))

    @property
    def expires_at(self) -> dt.datetime:
        """Angel One invalidates sessions at midnight IST regardless of age."""
        midnight = (self.created_at + dt.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        return min(midnight, self.created_at + dt.timedelta(hours=8))

    @property
    def expired(self) -> bool:
        return dt.datetime.now(IST) >= self.expires_at


class SessionStore:
    """In-memory store. Tokens never leave the server process.

    For multi-instance deployments swap for Redis with a TTL — but keep the
    property that the browser holds only an opaque id, never a broker token.
    """

    def __init__(self) -> None:
        self._data: dict[str, Session] = {}
        # nonce -> (issued_at, api_key). api_key is "" when the visitor chose
        # to use this server's shared default key rather than their own.
        self._nonces: dict[str, tuple[dt.datetime, str]] = {}
        self._lock = threading.Lock()

    # -- CSRF nonce ----------------------------------------------------------
    def issue_state(self, api_key: str = "") -> str:
        nonce = secrets.token_urlsafe(24)
        with self._lock:
            self._nonces[nonce] = (dt.datetime.now(IST), api_key)
            self._prune_nonces()
        return nonce

    def consume_state(self, nonce: str) -> Optional[str]:
        """Returns the api_key that was associated with this state (possibly
        an empty string, meaning "use the server default"), or None if the
        state was missing/expired/already used. Callers must check for None
        specifically — "" is a valid, successful result."""
        with self._lock:
            self._prune_nonces()
            entry = self._nonces.pop(nonce, None)
            return entry[1] if entry is not None else None

    def _prune_nonces(self) -> None:
        cutoff = dt.datetime.now(IST) - dt.timedelta(minutes=15)
        for k in [k for k, v in self._nonces.items() if v[0] < cutoff]:
            self._nonces.pop(k, None)

    # -- sessions --------------------------------------------------------------
    def create(self, auth_token: str, feed_token: str = "",
               refresh_token: str = "", client_code: str = "",
               api_key: str = "") -> Session:
        sid = secrets.token_urlsafe(32)
        s = Session(sid, auth_token, feed_token, refresh_token, client_code, api_key)
        with self._lock:
            self._data[sid] = s
            self._prune_sessions()
        log.info("session created (expires %s)", s.expires_at.isoformat())
        return s

    def get(self, sid: str | None) -> Session | None:
        if not sid:
            return None
        with self._lock:
            s = self._data.get(sid)
            if s and s.expired:
                self._data.pop(sid, None)
                return None
            return s

    def destroy(self, sid: str | None) -> None:
        if sid:
            with self._lock:
                self._data.pop(sid, None)

    def _prune_sessions(self) -> None:
        for k in [k for k, v in self._data.items() if v.expired]:
            self._data.pop(k, None)

    @property
    def active(self) -> int:
        with self._lock:
            return sum(1 for s in self._data.values() if not s.expired)


store = SessionStore()


# -- cookie signing ----------------------------------------------------------
def sign(sid: str) -> str:
    mac = hmac.new(_SECRET, sid.encode(), sha256).hexdigest()[:32]
    return f"{sid}.{mac}"


def unsign(value: str | None) -> str | None:
    if not value or "." not in value:
        return None
    sid, mac = value.rsplit(".", 1)
    expected = hmac.new(_SECRET, sid.encode(), sha256).hexdigest()[:32]
    return sid if hmac.compare_digest(mac, expected) else None


# -- login url -----------------------------------------------------------------
def login_url(redirect_url: str, state: str, api_key: str) -> str:
    """Build the Angel One Publisher Login URL.

    `api_key` must be the visitor's own SmartAPI app key. This server has no
    default to fall back to — SMARTAPI_API_KEY is not consulted here at all.
    """
    key = (api_key or "").strip()
    if not key:
        raise RuntimeError(
            "No SmartAPI app key supplied. Register one free at "
            "https://smartapi.angelone.in/ and paste it into the sign-in form.")
    return f"{PUBLISHER_LOGIN_URL}?" + urlencode({
        "api_key": key,
        "redirect_url": redirect_url,
        "state": state,
    })
