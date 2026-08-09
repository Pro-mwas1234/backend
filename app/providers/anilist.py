"""AniList GraphQL provider (primary metadata source).

Docs: https://docs.anilist.co  ·  Rate limit ~90 req/min (429 + Retry-After).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .. import schemas
from ..config import settings
from . import iso_date, strip_html

log = logging.getLogger("anidoom.anilist")

_MEDIA_CORE = """
id
idMal
title { romaji english native }
coverImage { large extraLarge color }
bannerImage
format
season
seasonYear
episodes
duration
status
averageScore
meanScore
popularity
favourites
genres
source
countryOfOrigin
isAdult
studios(isMain: true) { nodes { name isAnimationStudio } }
nextAiringEpisode { episode airingAt timeUntilAiring }
startDate { year month day }
endDate { year month day }
"""

_MEDIA_DETAIL = _MEDIA_CORE + """
description(asHtml: false)
siteUrl
hashtag
synonyms
trailer { id site thumbnail }
characters(sort: [ROLE, RELEVANCE], perPage: 12) {
  edges { role node { id name { full native } image { large } } }
}
staff(sort: RELEVANCE, perPage: 8) {
  edges { role node { id name { full native } image { large } } }
}
relations {
  edges {
    relationType(version: 2)
    node { id title { romaji english native } coverImage { large } format type status episodes meanScore }
  }
}
recommendations(sort: RATING_DESC, perPage: 8) {
  nodes { rating mediaRecommendation { id title { romaji english native } coverImage { large } format episodes status meanScore } }
}
"""


def _normalize(media: dict) -> dict:
    """Convert a raw AniList Media node into the unified catalog shape."""
    title = media.get("title") or {}
    return {
        "id": media["id"],
        "idMal": media.get("idMal"),
        "title": {
            "romaji": title.get("romaji"),
            "english": title.get("english"),
            "native": title.get("native"),
        },
        "synopsis": strip_html(media.get("description")),
        "coverImage": (media.get("coverImage") or {}).get("extraLarge")
        or (media.get("coverImage") or {}).get("large"),
        "bannerImage": media.get("bannerImage"),
        "format": media.get("format"),
        "season": media.get("season"),
        "seasonYear": media.get("seasonYear"),
        "episodes": media.get("episodes"),
        "duration": media.get("duration"),
        "status": media.get("status"),
        "score": media.get("averageScore"),
        "meanScore": media.get("meanScore"),
        "popularity": media.get("popularity"),
        "genres": media.get("genres") or [],
        "studios": [
            {"name": n.get("name"), "isAnimationStudio": n.get("isAnimationStudio")}
            for n in (media.get("studios") or {}).get("nodes", [])
        ],
        "nextAiringEpisode": media.get("nextAiringEpisode"),
        "startDate": iso_date(media.get("startDate")),
        "endDate": iso_date(media.get("endDate")),
        "isAdult": bool(media.get("isAdult")),
        "trailer": media.get("trailer"),
        "characters": _characters(media.get("characters")),
        "relations": _relations(media.get("relations")),
        "recommendations": _recommendations(media.get("recommendations")),
    }


def _characters(raw: dict | None) -> list[dict]:
    out = []
    for edge in (raw or {}).get("edges", []):
        node = edge.get("node") or {}
        out.append(
            {
                "id": node.get("id"),
                "name": (node.get("name") or {}).get("full"),
                "image": (node.get("image") or {}).get("large"),
                "role": edge.get("role"),
            }
        )
    return out


def _relations(raw: dict | None) -> list[dict]:
    out = []
    for edge in (raw or {}).get("edges", []):
        node = edge.get("node") or {}
        t = node.get("title") or {}
        out.append(
            {
                "relationType": edge.get("relationType"),
                "id": node.get("id"),
                "type": node.get("type"),
                "format": node.get("format"),
                "status": node.get("status"),
                "episodes": node.get("episodes"),
                "score": node.get("meanScore"),
                "title": {
                    "romaji": t.get("romaji"),
                    "english": t.get("english"),
                    "native": t.get("native"),
                },
                "coverImage": (node.get("coverImage") or {}).get("large"),
            }
        )
    return out


def _recommendations(raw: dict | None) -> list[dict]:
    out = []
    for node in (raw or {}).get("nodes", []):
        m = node.get("mediaRecommendation") or {}
        t = m.get("title") or {}
        out.append(
            {
                "rating": node.get("rating"),
                "id": m.get("id"),
                "format": m.get("format"),
                "episodes": m.get("episodes"),
                "status": m.get("status"),
                "score": m.get("meanScore"),
                "title": {
                    "romaji": t.get("romaji"),
                    "english": t.get("english"),
                    "native": t.get("native"),
                },
                "coverImage": (m.get("coverImage") or {}).get("large"),
            }
        )
    return out


class AniListProvider:
    name = "anilist"

    def __init__(self) -> None:
        # Lazy client: created on first use inside the running event loop
        # (avoids loop-binding issues when the app starts in uvicorn/TestClient).
        self._client: httpx.AsyncClient | None = None
        self._sem = asyncio.Semaphore(15)  # stay well under the 90/min limit

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=settings.ANILIST_URL,
                headers={"Accept": "application/json"},
                timeout=20.0,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _gql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        client = await self._get_client()
        async with self._sem:
            resp = await client.post(
                "/", json={"query": query, "variables": variables}
            )
        if resp.status_code == 429:
            retry = float(resp.headers.get("Retry-After", "1"))
            log.warning("AniList 429 — sleeping %.1fs", retry)
            await asyncio.sleep(min(retry, 30))
            return await self._gql(query, variables)
        resp.raise_for_status()
        body = resp.json()
        if body.get("errors"):
            raise RuntimeError(f"AniList error: {body['errors']}")
        return body.get("data", {})

    async def _page(self, query: str, variables: dict) -> dict:
        data = await self._gql(query, variables)
        page = data.get("Page", {})
        results = [_normalize(m) for m in page.get("media", [])]
        return {
            "page": variables.get("page", 1),
            "perPage": variables.get("per_page", 20),
            "total": page.get("pageInfo", {}).get("total", len(results)),
            "hasNextPage": page.get("pageInfo", {}).get("hasNextPage", False),
            "results": results,
        }

    # ── protocol ────────────────────────────────────────────────────
    async def search(self, query: str, page: int = 1, per_page: int = 20) -> dict:
        q = """
        query ($search: String, $page: Int, $perPage: Int) {
          Page(page: $page, perPage: $perPage) {
            pageInfo { total hasNextPage }
            media(search: $search, type: ANIME, sort: SEARCH_MATCH, isAdult: false) {
              %s
            }
          }
        }
        """ % _MEDIA_CORE
        return await self._page(q, {"search": query, "page": page, "per_page": per_page})

    async def details(self, media_id: int) -> dict | None:
        q = "query ($id: Int) { Media(id: $id, type: ANIME) { %s } }" % _MEDIA_DETAIL
        data = await self._gql(q, {"id": media_id})
        media = data.get("Media")
        return _normalize(media) if media else None

    async def trending(self, page: int = 1, per_page: int = 20) -> dict:
        q = """
        query ($page: Int, $perPage: Int) {
          Page(page: $page, perPage: $perPage) {
            pageInfo { total hasNextPage }
            media(type: ANIME, sort: TRENDING_DESC, isAdult: false) { %s }
          }
        }
        """ % _MEDIA_CORE
        return await self._page(q, {"page": page, "per_page": per_page})

    async def popular(self, page: int = 1, per_page: int = 20) -> dict:
        q = """
        query ($page: Int, $perPage: Int) {
          Page(page: $page, perPage: $perPage) {
            pageInfo { total hasNextPage }
            media(type: ANIME, sort: POPULARITY_DESC, isAdult: false) { %s }
          }
        }
        """ % _MEDIA_CORE
        return await self._page(q, {"page": page, "per_page": per_page})

    async def upcoming(self, page: int = 1, per_page: int = 20) -> dict:
        q = """
        query ($page: Int, $perPage: Int) {
          Page(page: $page, perPage: $perPage) {
            pageInfo { total hasNextPage }
            media(type: ANIME, status: NOT_YET_RELEASED, sort: POPULARITY_DESC, isAdult: false) { %s }
          }
        }
        """ % _MEDIA_CORE
        return await self._page(q, {"page": page, "per_page": per_page})

    async def recent(self, page: int = 1, per_page: int = 20) -> dict:
        q = """
        query ($page: Int, $perPage: Int) {
          Page(page: $page, perPage: $perPage) {
            pageInfo { total hasNextPage }
            media(type: ANIME, status: RELEASING, sort: TRENDING_DESC, isAdult: false) { %s }
          }
        }
        """ % _MEDIA_CORE
        return await self._page(q, {"page": page, "per_page": per_page})

    async def schedule(self, page: int = 1, per_page: int = 20) -> dict:
        q = """
        query ($page: Int, $perPage: Int) {
          Page(page: $page, perPage: $perPage) {
            pageInfo { total hasNextPage }
            airingSchedules(notYetAired: true, sort: TIME) {
              airingAt
              timeUntilAiring
              episode
              media { %s }
            }
          }
        }
        """ % _MEDIA_CORE
        data = await self._gql(q, {"page": page, "per_page": per_page})
        page_info = data.get("Page", {}).get("pageInfo", {})
        items = data.get("Page", {}).get("airingSchedules", [])
        results = []
        for item in items:
            media = _normalize(item.get("media", {}))
            media["nextAiringEpisode"] = {
                "episode": item.get("episode"),
                "airingAt": item.get("airingAt"),
                "timeUntilAiring": item.get("timeUntilAiring"),
            }
            results.append(media)
        return {
            "page": page,
            "perPage": per_page,
            "total": page_info.get("total", len(results)),
            "hasNextPage": page_info.get("hasNextPage", False),
            "results": results,
        }


anilist = AniListProvider()
