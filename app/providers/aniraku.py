"""Aniraku fallback provider — aniraku.tech's hosted backend.

Aniraku (one of our sibling projects) runs a backend at ANIRAKU_URL
(default: https://aniraku-backend-fhyy.onrender.com/api/v1). Under the hood it
resolves the same Miruro streams, but it handles Miruro's Cloudflare on its
side — so it's a useful fallback when OUR cf_clearance has expired.

Flow (mirrors Aniraku's own frontend):
  GET  /anime/{id}/episodes            → { episodes: [{number, title, thumbnail}] }
  GET  /servers?animeId=&episode=&lang → [{name, ...}]
  POST /stream {animeId, episode, provider, lang, quality} → { sources, headers }

We synthesize a provider named "aniraku" whose episode ids look like
`watch/aniraku/{anilistId}/sub/{ep}`; the /watch route resolves them through
this module. Enabled via ANIRAKU_ENABLED (default true).
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from ..config import settings

log = logging.getLogger("anidoom.aniraku")

TIMEOUT = 45.0  # Render free tier cold-starts are slow


class AnirakuError(Exception):
    """Aniraku backend unreachable or returned an error."""


class AnirakuProvider:
    name = "aniraku"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._sem = asyncio.Semaphore(4)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=settings.ANIRAKU_URL,
                headers={"Accept": "application/json"},
                timeout=TIMEOUT,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str) -> dict | list:
        client = await self._get_client()
        async with self._sem:
            try:
                resp = await client.get(path)
            except httpx.HTTPError as exc:
                raise AnirakuError(
                    f"Aniraku unreachable at {settings.ANIRAKU_URL} ({type(exc).__name__})"
                ) from exc
        if resp.status_code >= 400:
            raise AnirakuError(f"Aniraku error {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    # ── episodes (synthetic provider) ───────────────────────────────
    async def get_episodes(self, anilist_id: int) -> dict:
        data = await self._get(f"/anime/{anilist_id}/episodes")
        raw = data.get("episodes", []) if isinstance(data, dict) else []
        sub = []
        for ep in raw:
            if not isinstance(ep, dict) or ep.get("number") is None:
                continue
            num = _to_int(ep["number"])
            if num is None:
                continue
            sub.append(
                {
                    "id": f"watch/aniraku/{anilist_id}/sub/{num}",
                    "number": num,
                    "title": ep.get("title") or f"Episode {ep['number']}",
                    "image": ep.get("thumbnail"),
                    "airDate": None,
                    "duration": None,
                    "description": None,
                    "filler": False,
                }
            )
        return {
            "anilistId": anilist_id,
            "mappings": {},
            "providers": [{"name": "aniraku", "sub": sub, "dub": []}],
        }

    # ── sources ─────────────────────────────────────────────────────
    async def get_sources(self, episode_id: str) -> dict:
        parts = episode_id.split("/")
        if len(parts) < 4 or parts[0] != "watch":
            raise AnirakuError(f"Malformed episode id: {episode_id!r}")
        anilist_id = int(parts[2])
        category = parts[3]  # sub | dub
        ep_number = _to_int(parts[4]) if len(parts) > 4 else 1
        if ep_number is None:
            raise AnirakuError(f"Malformed episode number in {episode_id!r}")

        servers = await self._get(
            f"/servers?animeId={anilist_id}&episode={ep_number}&lang={category}"
        )
        if not isinstance(servers, list) or not servers:
            raise AnirakuError(f"No servers for {anilist_id} ep {ep_number} ({category})")
        provider = servers[0].get("name") or servers[0].get("provider") or "default"

        client = await self._get_client()
        async with self._sem:
            try:
                resp = await client.post(
                    "/stream",
                    json={
                        "animeId": anilist_id,
                        "episode": ep_number,
                        "provider": provider,
                        "lang": category,
                        "quality": "auto",
                        "refresh": False,
                    },
                )
            except httpx.HTTPError as exc:
                raise AnirakuError(f"Aniraku /stream failed: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            raise AnirakuError(f"Aniraku /stream {resp.status_code}: {resp.text[:200]}")
        data = resp.json()

        streams = [
            {
                "url": s.get("url"),
                "type": s.get("type", "hls"),
                "quality": s.get("quality"),
                "server": provider,
                "referer": (data.get("headers") or {}).get("Referer"),
            }
            for s in data.get("sources", [])
            if isinstance(s, dict) and s.get("url") and s.get("type") != "embed"
        ]
        subtitles = [
            {"file": s.get("url"), "label": s.get("label"), "kind": "captions"}
            for s in data.get("sources", [])
            if isinstance(s, dict) and s.get("subtitles")
            for t in (s.get("subtitles") or [])
            if isinstance(t, dict) and t.get("url")
        ]
        return {
            "episodeId": episode_id,
            "provider": "aniraku",
            "streams": streams,
            "subtitles": subtitles,
            "intro": {"start": 0, "end": 0},
            "outro": {"start": 0, "end": 0},
            "headers": data.get("headers") or {},
        }


aniraku = AnirakuProvider()


def _to_int(value) -> int | None:
    """Coerce an upstream episode number (int/float/str) to int, or None."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
