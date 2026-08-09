"""anidoom accounts — local register/login + AniList OAuth.

Storage is a single SQLite file (``backend/anidoom.db``) with two tables:
``users`` and ``sessions``. Passwords are PBKDF2-HMAC-SHA256 salted hashes
(stdlib only, no new dependencies). Sessions are opaque bearer tokens stored
in the browser's localStorage.

AniList OAuth requires a client registered at https://anilist.co/settings/developer
(``ANILIST_CLIENT_ID`` / ``ANILIST_CLIENT_SECRET`` in backend/.env). The
callback URL is derived from the incoming request host, so it works from any
host you register in AniList.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from ..config import settings

log = logging.getLogger("anidoom.auth")

router = APIRouter(prefix="/auth", tags=["auth"])

_DB_PATH = Path(settings.AUTH_DB_PATH).expanduser()
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PBKDF2_ITER = 310_000
SESSION_TTL_DAYS = 30
# Short-lived OAuth `state` tokens (CSRF protection), token → expiry.
_OAUTH_STATES: dict[str, float] = {}
_OAUTH_STATE_TTL = 600

ANILIST_AUTHORIZE_URL = "https://anilist.co/api/v2/oauth/authorize"
ANILIST_TOKEN_URL = "https://anilist.co/api/v2/oauth/token"
ANILIST_VIEWER_QUERY = "query { Viewer { id name avatar { large } } }"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _fetchone(sql: str, params=()) -> sqlite3.Row | None:
    conn = _db()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def _execute(sql: str, params=()) -> sqlite3.Cursor:
    conn = _db()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur
    finally:
        conn.close()


def _init_db() -> None:
    conn = _db()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT,
                provider TEXT NOT NULL DEFAULT 'local',
                provider_id TEXT,
                anilist_token TEXT,
                avatar TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


_init_db()


# ── password + session helpers ──────────────────────────────────────────
def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITER)
    return f"{salt.hex()}:{digest.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split(":", 1)
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), _PBKDF2_ITER
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    _execute(
        "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
        (token, user_id, _now()),
    )
    return token


def _user_from_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "email": row["email"],
        "avatar": row["avatar"] or "",
        "provider": row["provider"],
    }


def _bearer_user(request: Request) -> tuple[dict, str]:
    authorization = request.headers.get("authorization", "")
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    row = _fetchone(
        "SELECT u.*, s.created_at AS session_created "
        "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ?",
        (token,),
    )
    if not row:
        raise HTTPException(401, "Invalid or expired session")
    # Enforce a session TTL — ISO strings compare lexicographically.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SESSION_TTL_DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    if row["session_created"] < cutoff:
        _execute("DELETE FROM sessions WHERE token = ?", (token,))
        raise HTTPException(401, "Invalid or expired session")
    return _user_from_row(row), token


def _anilist_callback_url(request: Request) -> str:
    return f"{str(request.base_url).rstrip('/')}/api/auth/anilist/callback"


def _frontend_origin(request: Request) -> str:
    """The frontend origin the user came through (vite proxy preserves it)."""
    return str(request.base_url).rstrip("/") or settings.FRONTEND_URL


def _anilist_redirect(request: Request, error: str) -> RedirectResponse:
    return RedirectResponse(
        f"{_frontend_origin(request)}/auth/callback?error={urllib.parse.quote(error)}"
    )


# ── models ──────────────────────────────────────────────────────────────
class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    email: str = Field(max_length=128)
    password: str = Field(min_length=6, max_length=128)


class LoginIn(BaseModel):
    identifier: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=128)


# ── local accounts ──────────────────────────────────────────────────────
@router.post("/register")
def register(body: RegisterIn):
    username = body.username.strip()
    email = body.email.strip().lower()
    if not username:
        raise HTTPException(422, "Username is required")
    if not _EMAIL_RE.match(email):
        raise HTTPException(422, "Enter a valid email address")
    try:
        cur = _execute(
            "INSERT INTO users (username, email, password_hash, provider, created_at) "
            "VALUES (?, ?, ?, 'local', ?)",
            (username, email, _hash_password(body.password), _now()),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(409, "That username or email is already registered")
    uid = cur.lastrowid
    token = _create_session(uid)
    return {
        "token": token,
        "user": {"id": uid, "username": username, "email": email, "avatar": "", "provider": "local"},
    }


@router.post("/login")
def login(body: LoginIn):
    identifier = body.identifier.strip()
    row = _fetchone(
        "SELECT * FROM users WHERE username = ? OR email = ?",
        (identifier, identifier.lower()),
    )
    if not row or not row["password_hash"] or not _verify_password(body.password, row["password_hash"]):
        raise HTTPException(401, "Wrong username or password")
    token = _create_session(row["id"])
    return {"token": token, "user": _user_from_row(row)}


@router.get("/me")
def me(request: Request):
    user, _ = _bearer_user(request)
    return user


@router.post("/logout")
def logout(request: Request):
    _, token = _bearer_user(request)
    _execute("DELETE FROM sessions WHERE token = ?", (token,))
    return {"ok": True}


# ── AniList OAuth ───────────────────────────────────────────────────────
@router.get("/anilist/login")
def anilist_login(request: Request):
    if not settings.ANILIST_CLIENT_ID:
        # Redirect (not raw JSON) so the SPA shows a friendly error page.
        return _anilist_redirect(
            request,
            "AniList sign-in isn't configured yet — set ANILIST_CLIENT_ID in backend/.env "
            "(create a client at https://anilist.co/settings/developer).",
        )
    state = secrets.token_urlsafe(24)
    _OAUTH_STATES[state] = time.time() + _OAUTH_STATE_TTL
    params = urllib.parse.urlencode(
        {
            "client_id": settings.ANILIST_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": _anilist_callback_url(request),
            "state": state,
        }
    )
    return RedirectResponse(f"{ANILIST_AUTHORIZE_URL}?{params}")


@router.get("/anilist/callback")
def anilist_callback(
    request: Request,
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
):
    if error:
        return _anilist_redirect(request, error)
    if not settings.ANILIST_CLIENT_ID or not settings.ANILIST_CLIENT_SECRET:
        return _anilist_redirect(request, "AniList sign-in isn't configured")
    if not code:
        return _anilist_redirect(request, "AniList login failed (no code)")
    # Verify the state token we issued — blocks login-CSRF.
    expected = _OAUTH_STATES.pop(state, None) if state else None
    if expected is None or expected < time.time():
        return _anilist_redirect(request, "AniList login failed (stale session)")

    # Exchange the code for an access token.
    payload = urllib.parse.urlencode(
        {
            "client_id": settings.ANILIST_CLIENT_ID,
            "client_secret": settings.ANILIST_CLIENT_SECRET,
            "code": code,
            "redirect_uri": _anilist_callback_url(request),
            "grant_type": "authorization_code",
        }
    ).encode()
    try:
        token_req = urllib.request.Request(
            ANILIST_TOKEN_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(token_req, timeout=30) as resp:
            anilist_token = json.loads(resp.read()).get("access_token")
        if not anilist_token:
            raise RuntimeError("no access_token in response")
    except Exception as exc:  # noqa: BLE001
        log.warning("AniList token exchange failed: %s", exc)
        return _anilist_redirect(request, "AniList login failed (token exchange)")

    # Fetch the viewer profile to key the local account by AniList id.
    try:
        gql_req = urllib.request.Request(
            settings.ANILIST_URL,
            data=json.dumps({"query": ANILIST_VIEWER_QUERY}).encode(),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {anilist_token}",
            },
        )
        with urllib.request.urlopen(gql_req, timeout=30) as resp:
            viewer = (json.loads(resp.read()).get("data") or {}).get("Viewer") or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("AniList viewer fetch failed: %s", exc)
        return _anilist_redirect(request, "AniList login failed (profile fetch)")

    vid = str(viewer.get("id") or "")
    if not vid:
        return _anilist_redirect(request, "AniList login failed (no profile)")
    name = (viewer.get("name") or f"anilist-{vid}").strip()
    avatar = (viewer.get("avatar") or {}).get("large") or ""

    # Upsert the local account bound to this AniList id.
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE provider = 'anilist' AND provider_id = ?", (vid,)
        ).fetchone()
        if row:
            # Keep the username unique even on rename (suffix on collision).
            base_name, candidate, suffix = name, name, 1
            while conn.execute(
                "SELECT 1 FROM users WHERE username = ? AND id != ?", (candidate, row["id"])
            ).fetchone():
                suffix += 1
                candidate = f"{base_name}{suffix}"
            conn.execute(
                "UPDATE users SET anilist_token = ?, avatar = ?, username = ? WHERE id = ?",
                (anilist_token, avatar, candidate, row["id"]),
            )
            uid = row["id"]
        else:
            base_name, candidate, suffix = name, name, 1
            while conn.execute("SELECT 1 FROM users WHERE username = ?", (candidate,)).fetchone():
                suffix += 1
                candidate = f"{base_name}{suffix}"
            cur = conn.execute(
                "INSERT INTO users (username, email, provider, provider_id, anilist_token, avatar, created_at) "
                "VALUES (?, ?, 'anilist', ?, ?, ?, ?)",
                (candidate, f"anilist-{vid}@anilist.local", vid, anilist_token, avatar, _now()),
            )
            uid = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    origin = _frontend_origin(request)
    return RedirectResponse(f"{origin}/auth/callback?token={_create_session(uid)}")
