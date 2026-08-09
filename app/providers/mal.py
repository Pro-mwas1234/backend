"""MyAnimeList provider via the Jikan API (secondary enrichment).

Jikan rate limits: 3 req/s, 60 req/min → the provider self-throttles to ~2 req/s
and relies on the app-level TTL cache.

Endpoints used:
  GET /anime/{mal_id}/full   → full MAL details (enrichment)
  GET /anime?q=…             → search fallback
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

from ..config import settings
from . import strip_html

log = logging.getLogger("anidoom.mal")

_MIN_INTERVAL = 0.35  # ~2.8 req/s ceiling


class JikanProvider:
    name = "mal"

    def __init__(self) -> None:
        # Lazy client (see providers/anilist.py for rationale).
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        self._last_request = 0.0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=settings.JIKAN_URL,
                headers={"Accept": "application/json"},
                timeout=15.0,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str, params: dict | None = None) -> dict:
        """Rate-limited GET returning parsed JSON (or {} on 404/429)."""
        client = await self._get_client()
        async with self._lock:
            wait = _MIN_INTERVAL - (time.monotonic() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            resp = await client.get(path, params=params)
            self._last_request = time.monotonic()
        if resp.status_code == 429:
            retry = float(resp.headers.get("Retry-After", "2"))
            log.warning("Jikan 429 — sleeping %.1fs", retry)
            await asyncio.sleep(min(retry, 20))
            return await self._get(path, params)
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        return resp.json()

    # ── enrichment ──────────────────────────────────────────────────
    async def enrich(self, mal_id: int) -> dict | None:
        """Fetch /anime/{id}/full and return the fields we care about."""
        data = await self._get(f"/anime/{mal_id}/full")
        anime = data.get("data")
        if not anime:
            return None
        images = (anime.get("images") or {}).get("jpg") or {}
        return {
            "score": anime.get("score"),
            "scoredBy": anime.get("scored_by"),
            "members": anime.get("members"),
            "favorites": anime.get("favorites"),
            "rank": anime.get("rank"),
            "popularity": anime.get("popularity"),
            "status": anime.get("status"),
            "episodes": anime.get("episodes"),
            "rating": anime.get("rating"),  # e.g. PG-13
            "synopsis": strip_html(anime.get("synopsis")),
            "url": anime.get("url"),
            "image": images.get("large_image_url"),
            "titles": [
                t.get("title")
                for t in anime.get("titles", [])
                if t.get("type") in ("Default", "English", "Japanese")
            ],
        }

    # ── search fallback ─────────────────────────────────────────────
    async def search(self, query: str, page: int = 1, per_page: int = 20) -> dict:
        data = await self._get("/anime", {"q": query, "page": page, "limit": per_page})
        results = []
        for anime in data.get("data", []):
            images = (anime.get("images") or {}).get("jpg") or {}
            results.append(
                {
                    "id": None,  # MAL id ≠ AniList id; catalog uses AniList ids
                    "mal_id": anime.get("mal_id"),
                    "title": {"romaji": anime.get("title"), "english": None, "native": None},
                    "synopsis": strip_html(anime.get("synopsis")),
                    "coverImage": images.get("large_image_url"),
                    "format": anime.get("type"),
                    "episodes": anime.get("episodes"),
                    "status": anime.get("status"),
                    "score": int((anime.get("score") or 0) * 10),
                    "genres": [g.get("name") for g in anime.get("genres", [])],
                    "source": "mal",
                }
            )
        pag = data.get("pagination", {})
        return {
            "page": page,
            "perPage": per_page,
            "total": pag.get("items", {}).get("total", len(results)),
            "hasNextPage": pag.get("has_next_page", False),
            "results": results,
        }


jikan = JikanProvider()
