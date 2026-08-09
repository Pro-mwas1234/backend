"""Catalog service: merges metadata providers into one shape.

Current chain: AniList (primary) → Jikan/MAL (enrichment on detail pages).
Malkan is a documented no-op slot (see app/providers/malkan.py).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..cache import cache
from ..config import settings
from ..providers.anilist import anilist
from ..providers.mal import jikan

log = logging.getLogger("anidoom.catalog")


# ── helpers ────────────────────────────────────────────────────────
async def _cache(key: str, factory):
    return await cache.get_or_set(key, factory)


def _merge_mal(media: dict, mal: dict | None) -> dict:
    """Fold MAL fields into the unified media dict (without overriding AniList)."""
    if not mal:
        return media
    media = dict(media)
    media["mal"] = mal
    if not media.get("synopsis") and mal.get("synopsis"):
        media["synopsis"] = mal["synopsis"]
    if not media.get("coverImage") and mal.get("image"):
        media["coverImage"] = mal["image"]
    return media


# ── search ─────────────────────────────────────────────────────────
async def search(
    query: str, page: int = 1, per_page: int = 20, enrich_mal: bool = False
) -> dict:
    """AniList search; optionally enrich results with MAL data (`enrich_mal`)."""
    key = f"search:{query.lower()}:{page}:{per_page}:{enrich_mal}"

    async def factory() -> dict:
        data = await anilist.search(query, page=page, per_page=per_page)
        if enrich_mal:
            data["results"] = await _enrich_many(data["results"], cap=8)
        return data

    return await _cache(key, factory)


async def _enrich_many(results: list[dict], cap: int = 8) -> list[dict]:
    """Enrich up to `cap` results with MAL data, in parallel."""
    targets = [r for r in results if r.get("idMal")][:cap]

    async def one(r: dict) -> dict:
        try:
            mal = await jikan.enrich(r["idMal"])
            return _merge_mal(r, mal)
        except Exception as exc:
            log.warning("MAL enrich failed for %s: %s", r.get("id"), exc)
            return r

    enriched = await asyncio.gather(*[one(r) for r in targets])
    enriched_by_id = {r["id"]: r for r in enriched}
    return [enriched_by_id.get(r["id"], r) for r in results]


# ── details ────────────────────────────────────────────────────────
async def details(media_id: int) -> dict | None:
    """Full anime details from AniList, merged with MAL enrichment."""
    key = f"details:{media_id}"

    async def factory() -> dict | None:
        media = await anilist.details(media_id)
        if not media:
            return None
        if media.get("idMal"):
            try:
                mal = await jikan.enrich(media["idMal"])
                media = _merge_mal(media, mal)
            except Exception as exc:
                log.warning("MAL enrich failed for details %s: %s", media_id, exc)
        return media

    return await _cache(key, factory)


# ── collections (thin pass-throughs with caching) ──────────────────
async def trending(page: int = 1, per_page: int = 20) -> dict:
    return await _cache(f"trending:{page}:{per_page}", lambda: anilist.trending(page, per_page))


async def popular(page: int = 1, per_page: int = 20) -> dict:
    return await _cache(f"popular:{page}:{per_page}", lambda: anilist.popular(page, per_page))


async def upcoming(page: int = 1, per_page: int = 20) -> dict:
    return await _cache(f"upcoming:{page}:{per_page}", lambda: anilist.upcoming(page, per_page))


async def recent(page: int = 1, per_page: int = 20) -> dict:
    return await _cache(f"recent:{page}:{per_page}", lambda: anilist.recent(page, per_page))


async def schedule(page: int = 1, per_page: int = 20) -> dict:
    return await _cache(f"schedule:{page}:{per_page}", lambda: anilist.schedule(page, per_page))


# ── misc ───────────────────────────────────────────────────────────
async def mal_raw(media_id: int) -> dict | None:
    """Return raw Jikan /anime/{idMal}/full for an AniList id."""
    media = await details(media_id)
    if not media or not media.get("idMal"):
        return None
    return await _cache(f"mal:{media['idMal']}", lambda: jikan.enrich(media["idMal"]))


async def clear_cache() -> int:
    return await cache.clear()
