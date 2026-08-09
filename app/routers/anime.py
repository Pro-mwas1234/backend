"""Anime catalog + episodes."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

import asyncio

from .. import services
from ..cache import cache
from ..config import settings
from ..providers.aniraku import AnirakuError, aniraku
from ..providers.anivexa import anivexa
from ..providers.miruro import get_episodes
from ..schemas import EpisodesResponse, Media, Page

log = logging.getLogger("anidoom.anime")

router = APIRouter(prefix="/anime", tags=["anime"])


def _page(data: dict) -> Page:
    return Page(**data)


@router.get("/search", response_model=Page)
async def search(
    q: str = Query(..., min_length=1, description="Search query"),
    page: int = Query(1, ge=1),
    perPage: int = Query(20, ge=1, le=50),
    mal: bool = Query(False, description="Enrich results with MAL metadata (slower)"),
):
    return _page(await services.search(q, page=page, per_page=perPage, enrich_mal=mal))


@router.get("/trending", response_model=Page)
async def trending(page: int = Query(1, ge=1), perPage: int = Query(20, ge=1, le=50)):
    return _page(await services.trending(page, perPage))


@router.get("/popular", response_model=Page)
async def popular(page: int = Query(1, ge=1), perPage: int = Query(20, ge=1, le=50)):
    return _page(await services.popular(page, perPage))


@router.get("/upcoming", response_model=Page)
async def upcoming(page: int = Query(1, ge=1), perPage: int = Query(20, ge=1, le=50)):
    return _page(await services.upcoming(page, perPage))


@router.get("/recent", response_model=Page)
async def recent(page: int = Query(1, ge=1), perPage: int = Query(20, ge=1, le=50)):
    return _page(await services.recent(page, perPage))


@router.get("/schedule", response_model=Page)
async def schedule(page: int = Query(1, ge=1), perPage: int = Query(20, ge=1, le=50)):
    return _page(await services.schedule(page, perPage))


@router.get("/{anime_id}", response_model=Media)
async def details(anime_id: int):
    media = await services.details(anime_id)
    if not media:
        raise HTTPException(404, f"Anime {anime_id} not found on AniList")
    return Media(**media)


@router.get("/{anime_id}/mal")
async def mal_details(anime_id: int):
    raw = await services.mal_raw(anime_id)
    if not raw:
        raise HTTPException(404, "No MAL mapping (idMal) for this anime")
    return raw


def _merge_providers(blocks: list[dict]) -> list[dict]:
    """Merge provider dicts from multiple sources, deduping by name."""
    merged: dict[str, dict] = {}
    for block in blocks:
        for prov in block.get("providers", []):
            name = prov.get("name")
            if not name:
                continue
            if name in merged:
                # Keep whichever has more episodes for this audio type.
                old = merged[name]
                if len(prov.get("sub", [])) > len(old.get("sub", [])):
                    old["sub"] = prov["sub"]
                if len(prov.get("dub", [])) > len(old.get("dub", [])):
                    old["dub"] = prov["dub"]
            else:
                merged[name] = prov
    return list(merged.values())


@router.get("/{anime_id}/episodes", response_model=EpisodesResponse)
async def episodes(anime_id: int):
    """Episode lists per provider/audio from Anivexa (primary) with an
    Aniraku fallback when Anivexa has nothing. Miruro is only queried when
    MIRURO_ENABLED=true (it 403s upstream without a fresh cf_clearance).

    Cached for CACHE_TTL — the Anivexa sweep is slow (multi-provider scrape).
    """
    async def miruro_block() -> dict:
        return await get_episodes(anime_id)

    async def anivexa_block() -> dict:
        return await anivexa.get_episodes(anime_id)

    async def factory() -> dict:
        named_tasks = []
        if settings.MIRURO_ENABLED:
            named_tasks.append(("miruro", miruro_block()))
        named_tasks.append(("anivexa", anivexa_block()))

        results = await asyncio.gather(
            *(task for _, task in named_tasks), return_exceptions=True
        )
        blocks = []
        for name, res in zip((n for n, _ in named_tasks), results):
            if isinstance(res, Exception):
                log.warning("%s episodes failed for %s: %s", name, anime_id, res)
                continue
            blocks.append(res)

        providers = _merge_providers(blocks)
        mappings = blocks[0].get("mappings", {}) if blocks else {}

        has_episodes = any(p.get("sub") or p.get("dub") for p in providers)
        if (not providers or not has_episodes) and settings.ANIRAKU_ENABLED:
            try:
                aniraku_block = await aniraku.get_episodes(anime_id)
                providers = _merge_providers(blocks + [aniraku_block])
                if providers:
                    mappings = mappings or aniraku_block.get("mappings", {})
            except AnirakuError as exc:
                log.warning("Aniraku fallback failed for %s: %s", anime_id, exc)

        if not providers:
            raise HTTPException(404, f"No stream providers found for anime {anime_id}")
        return {"anilistId": anime_id, "mappings": mappings, "providers": providers}

    # Only successful results are cached; 404s re-run next time.
    data = await cache.get_or_set(f"episodes:{anime_id}", factory)
    return EpisodesResponse(**data)
