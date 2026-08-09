"""Miruro pipe provider (streaming).

Resolves episode lists and direct m3u8 sources from Miruro's Cloudflare-protected
pipe endpoint. Protocol (reverse-engineered from the open-source Miruro-API
project, https://github.com/walterwhite-69/Miruro-API):

  Request : GET https://{domain}/api/secure/pipe?e=<payload>
            payload = base64url( json { path, method, query, body, version } ), '=' stripped
  Response: gzip.decompress( base64url_decode(body) )  → JSON

Cloudflare notes (see docs/CLOUDFLARE.md):
  * Requires a valid cf_clearance cookie + browser-grade TLS (curl_cffi chrome110).
  * Binds clearance to IP + User-Agent + TLS fingerprint — mint from the same
    machine that runs this backend.
  * Datacenter IPs (Vercel/Render/CF Workers) get hard 403s.
"""
from __future__ import annotations

import asyncio
import base64
import gzip
import json
import logging

from curl_cffi.requests import AsyncSession

from ..config import settings

log = logging.getLogger("anidoom.miruro")


class MiruroError(Exception):
    """Generic Miruro pipe failure."""


class CloudflareBlockedError(MiruroError):
    """Miruro returned a Cloudflare challenge/block — re-mint cf_clearance."""


# ── pipe (de)serialization ─────────────────────────────────────────
def _encode_pipe_request(payload: dict) -> str:
    return (
        base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8"))
        .decode("utf-8")
        .rstrip("=")
    )


def _decode_pipe_response(encoded_str: str) -> dict:
    encoded_str += "=" * (4 - len(encoded_str) % 4)
    compressed = base64.urlsafe_b64decode(encoded_str)
    return json.loads(gzip.decompress(compressed).decode("utf-8"))


def _translate_id(encoded_id: str) -> str:
    """Decode base64url episode ids that contain a ':' separator."""
    try:
        decoded = base64.urlsafe_b64decode(
            encoded_id + "=" * (4 - len(encoded_id) % 4)
        ).decode("utf-8")
        return decoded if ":" in decoded else encoded_id
    except Exception:
        return encoded_id


def _is_challenge(status: int, headers, text: str) -> bool:
    if headers.get("cf-mitigated", "").lower() == "challenge":
        return True
    if status in (403, 429, 503):
        probe = text[:2000].lower()
        if "challenge-platform" in probe or "just a moment" in probe:
            return True
    return False


# ── HTTP layer (curl_cffi, chrome110 TLS impersonation) ────────────
_session: AsyncSession | None = None
_session_lock = asyncio.Lock()


async def _get_session() -> AsyncSession:
    global _session
    if _session is None:
        async with _session_lock:
            if _session is None:
                _session = AsyncSession(impersonate="chrome110", timeout=20)
    return _session


def _headers_for(domain: str) -> dict:
    return {
        "User-Agent": settings.CF_USER_AGENT,
        "Referer": f"https://{domain}/",
        "Origin": f"https://{domain}",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "sec-fetch-site": "same-origin",
        "sec-fetch-mode": "cors",
        "sec-fetch-dest": "empty",
        "sec-ch-ua": '"Chromium";v="110", "Not A(Brand";v="24", "Google Chrome";v="110"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }


async def _pipe(path: str, query: dict | None = None) -> dict:
    """Call the pipe across all configured domains, first success wins."""
    payload = {
        "path": path,
        "method": "GET",
        "query": query,
        "body": None,
        "version": settings.PIPE_VERSION,
    }
    encoded = _encode_pipe_request(payload)
    session = await _get_session()
    errors: list[str] = []

    for domain in settings.MIRURO_DOMAINS:
        url = f"https://{domain}/api/secure/pipe?e={encoded}"
        try:
            resp = await session.get(
                url,
                headers=_headers_for(domain),
                cookies={"cf_clearance": settings.CF_CLEARANCE}
                if settings.CF_CLEARANCE
                else None,
            )
            if _is_challenge(resp.status_code, resp.headers, resp.text):
                errors.append(f"{domain}: Cloudflare challenge (status {resp.status_code})")
                continue
            if resp.status_code != 200:
                errors.append(f"{domain}: HTTP {resp.status_code} — {resp.text[:200]}")
                continue
            return _decode_pipe_response(resp.text.strip())
        except Exception as exc:  # network / TLS errors
            errors.append(f"{domain}: {type(exc).__name__}: {exc}")
            log.warning("Miruro pipe failed on %s: %s", domain, exc)

    raise CloudflareBlockedError(
        "All Miruro domains failed. If the last error mentions a Cloudflare "
        "challenge, re-mint your cf_clearance cookie (docs/CLOUDFLARE.md). "
        f"Details: {' | '.join(errors)}"
    )


# ── normalization ──────────────────────────────────────────────────
def _normalize_episode(
    ep: dict, provider: str, anilist_id: int, category: str
) -> dict:
    raw_id = ep.get("id") or ""
    if raw_id.startswith("watch/"):
        eid = raw_id
    else:
        translated = _translate_id(raw_id)
        prefix = (
            translated.split(":")[0]
            if ":" in translated
            else (translated or f"{category}-{ep.get('number')}")
        )
        eid = f"watch/{provider}/{anilist_id}/{category}/{prefix}-{ep.get('number')}"
    return {
        "id": eid,
        "number": ep.get("number"),
        "title": ep.get("title"),
        "image": ep.get("image"),
        "airDate": ep.get("airDate"),
        "duration": ep.get("duration"),
        "description": ep.get("description"),
        "filler": bool(ep.get("filler")),
    }


def _normalize_episodes(data: dict, anilist_id: int) -> dict:
    providers = []
    for name, pdata in data.get("providers", {}).items():
        if not isinstance(pdata, dict):
            continue
        eps = pdata.get("episodes", {})
        if isinstance(eps, list):  # tolerate bare lists
            eps = {"sub": eps}
        sub = [
            _normalize_episode(e, name, anilist_id, "sub")
            for e in eps.get("sub", [])
            if isinstance(e, dict)
        ]
        dub = [
            _normalize_episode(e, name, anilist_id, "dub")
            for e in eps.get("dub", [])
            if isinstance(e, dict)
        ]
        if sub or dub:
            providers.append({"name": name, "sub": sub, "dub": dub})
    return {
        "anilistId": anilist_id,
        "mappings": data.get("mappings", {}),
        "providers": providers,
    }


def _normalize_sources(data: dict, episode_id: str) -> dict:
    streams = [
        {
            "url": s.get("url"),
            "type": s.get("type", "hls"),
            "quality": s.get("quality"),
        }
        for s in data.get("streams", [])
        if isinstance(s, dict) and s.get("url")
    ]
    subtitles = [
        {
            "file": t.get("file"),
            "label": t.get("label"),
            "kind": t.get("kind", "captions"),
        }
        for t in data.get("subtitles", [])
        if isinstance(t, dict) and t.get("file")
    ]
    parts = episode_id.split("/")
    return {
        "episodeId": episode_id,
        "provider": parts[1] if len(parts) > 1 else None,
        "streams": streams,
        "subtitles": subtitles,
        "intro": data.get("intro") or {"start": 0, "end": 0},
        "outro": data.get("outro") or {"start": 0, "end": 0},
    }


# ── public API ─────────────────────────────────────────────────────
async def get_episodes(anilist_id: int) -> dict:
    """Episode lists per provider/audio type for an AniList id."""
    data = await _pipe("episodes", {"anilistId": anilist_id})
    return _normalize_episodes(data, anilist_id)


async def get_sources(episode_id: str) -> dict:
    """Resolve m3u8 streams/subtitles for an episode id (watch/...)."""
    if not episode_id.startswith("watch/"):
        raise MiruroError(
            f"Malformed episode id: {episode_id!r} (expected 'watch/<provider>/...')"
        )
    data = await _pipe(episode_id)
    return _normalize_sources(data, episode_id)
