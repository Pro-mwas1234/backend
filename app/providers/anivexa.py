"""Anivexa streaming provider — proxies the vendored Anivexa-API sidecar.

Anivexa-API (walterwhite-69/Anivexa-API) is a zero-dependency Node.js REST
service aggregating 13 anime providers (allmanga, reanime, anikoto, animegg,
anineko, anidbapp, 2dhive, animenosub, anizone, anibd, senshi, kaa,
animedunya) keyed by AniList id. Run it with `anivexa-api/run.bat` on
ANIVEXA_URL (default :8002).

Its episode ids already match our canonical format
`watch/{provider}/{anilistId}/{sub|dub}/{provider}-{ep}`, so /watch routes
forward 1:1. Watch responses include per-stream `referer`/`subtitles` and
`intro`/`outro` timestamps.

See docs/DATA_SOURCES.md for notes.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from ..config import settings

log = logging.getLogger("anidoom.anivexa")

TIMEOUT = 90.0  # first /episodes call sweeps all 13 providers


class AnivexaError(Exception):
    """Sidecar unreachable or returned an error."""


class AnivexaProvider:
    name = "anivexa"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._sem = asyncio.Semaphore(6)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=settings.ANIVEXA_URL,
                headers={"Accept": "application/json"},
                # Short connect timeout: if the sidecar is down we want a fast
                # 502 (and a quick frontend auto-switch), not a 90s hang.
                timeout=httpx.Timeout(TIMEOUT, connect=5.0),
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _call(self, path: str) -> dict:
        client = await self._get_client()
        async with self._sem:
            try:
                resp = await client.get(path)
            except httpx.HTTPError as exc:
                raise AnivexaError(
                    f"Anivexa unreachable at {settings.ANIVEXA_URL} "
                    f"({type(exc).__name__}). Start it with anivexa-api/run.bat."
                ) from exc
        if resp.status_code >= 400:
            raise AnivexaError(f"Anivexa error {resp.status_code} for {path}: {resp.text[:200]}")
        return resp.json()

    # ── episodes ────────────────────────────────────────────────────
    async def get_episodes(self, anilist_id: int) -> dict:
        data = await self._call(f"/episodes/{anilist_id}")
        providers = []
        raw = data.get("providers") or data
        if not isinstance(raw, dict):
            return {"anilistId": anilist_id, "mappings": {}, "providers": []}
        for name, block in raw.items():
            if not isinstance(block, dict) or "episodes" not in block:
                continue
            eps = block["episodes"]
            if not isinstance(eps, dict):
                continue
            sub = [_episode(e) for e in eps.get("sub", []) if isinstance(e, dict)]
            dub = [_episode(e) for e in eps.get("dub", []) if isinstance(e, dict)]
            if sub or dub:
                providers.append({"name": name, "sub": sub, "dub": dub})
        return {
            "anilistId": anilist_id,
            "mappings": data.get("mappings") or {},
            "providers": providers,
        }

    # ── sources ─────────────────────────────────────────────────────
    async def get_sources(self, episode_id: str) -> dict:
        data = await self._call(f"/{episode_id}")
        streams = [
            {
                "url": s.get("url"),
                "type": s.get("type", "hls"),
                "quality": s.get("quality"),
                "server": s.get("server"),
                # Referer the CDN expects — surfaced so the HLS worker can use it.
                "referer": s.get("referer"),
            }
            for s in data.get("streams", [])
            if isinstance(s, dict) and s.get("url") and s.get("type") != "embed"
        ]
        # Intro/outro live on individual streams; prefer the first hls one.
        first = next(
            (s for s in data.get("streams", []) if isinstance(s, dict) and s.get("intro")),
            None,
        )
        subtitles = [
            {"file": t.get("url"), "label": t.get("label") or t.get("srclang"), "kind": "captions"}
            for t in data.get("subtitles", [])
            if isinstance(t, dict) and t.get("url")
        ]
        parts = episode_id.split("/")
        return {
            "episodeId": episode_id,
            "provider": parts[1] if len(parts) > 1 else None,
            "streams": streams,
            "subtitles": subtitles,
            "intro": (first or {}).get("intro") or {"start": 0, "end": 0},
            "outro": (first or {}).get("outro") or {"start": 0, "end": 0},
            "headers": data.get("headers") or {},
        }


def _episode(ep: dict) -> dict:
    return {
        "id": ep.get("id"),
        "number": ep.get("number"),
        "title": ep.get("title"),
        "image": ep.get("image"),
        "airDate": ep.get("airDate"),
        "duration": ep.get("duration"),
        "description": ep.get("description"),
        "filler": bool(ep.get("filler")),
    }


anivexa = AnivexaProvider()
