"""cf_clearance helpers.

Cloudflare binds a clearance cookie to (IP, User-Agent, TLS fingerprint).
The backend reuses the cookie configured in `CF_CLEARANCE` env var through
curl_cffi's chrome110 TLS impersonation so the fingerprint matches.
"""
from __future__ import annotations

from ..config import settings


def has_clearance() -> bool:
    return bool(settings.CF_CLEARANCE)


def cookies() -> dict | None:
    """Return the cookie dict to attach to Miruro requests."""
    if not settings.CF_CLEARANCE:
        return None
    return {"cf_clearance": settings.CF_CLEARANCE}


def hint() -> str:
    """Human-readable guidance when Miruro blocks us."""
    return (
        "Miruro is behind Cloudflare. Set CF_CLEARANCE in backend/.env — mint it "
        "with `python scripts/mint_cf_clearance.py` or copy it from your browser "
        "(DevTools → Application → Cookies → miruro.tv). It must be minted from "
        "the same IP/User-Agent you run the backend with. See docs/CLOUDFLARE.md."
    )
