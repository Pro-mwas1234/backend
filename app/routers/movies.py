"""Movies & TV catalog + streaming (backed by the Moviebox-API sidecar)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from ..cache import cache, short_cache
from ..providers.moviebox import (
    MovieboxError,
    MovieboxNotFoundError,
    moviebox,
)

log = logging.getLogger("anidoom.movies")

# Literal routes (/home, /catalog, /search, /suggest) are declared before the
# parameterized /{slug} so they win the match.
router = APIRouter(prefix="/movies", tags=["movies"])

_TABS = {"movie": 2, "tv": 5, "animation": 8}
_SORTS = ("RECOMMEND", "HOT", "NEW", "RATING", "POPULAR", "LATEST")


def _http_error(exc: MovieboxError) -> HTTPException:
    log.warning("Moviebox error: %s", exc)
    if isinstance(exc, MovieboxNotFoundError):
        return HTTPException(404, detail=str(exc))
    return HTTPException(503, detail=str(exc))


@router.get("/home")
async def home():
    """Homepage: banner + genre/subject rows from MovieBox."""
    try:
        return await cache.get_or_set("movies:home", moviebox.home)
    except MovieboxError as exc:
        raise _http_error(exc) from exc


@router.get("/catalog")
async def catalog(
    type: str = Query("movie", pattern="^(movie|tv|animation)$"),
    page: int = Query(1, ge=1),
    sort: str = Query("RECOMMEND", description="One of RECOMMEND/HOT/NEW/RATING/POPULAR/LATEST"),
):
    """Paged catalog for a tab. `sort` maps to MovieBox's own filter sorts."""
    if sort not in _SORTS:
        raise HTTPException(400, f"sort must be one of {_SORTS}")
    key = f"movies:catalog:{type}:{page}:{sort}"

    async def factory() -> dict:
        return await moviebox.catalog(_TABS[type], page, sort)

    try:
        return await cache.get_or_set(key, factory)
    except MovieboxError as exc:
        raise _http_error(exc) from exc


@router.get("/search")
async def search(q: str = Query(..., min_length=1, max_length=200), page: int = Query(1, ge=1)):
    """Full-text search across movies, series and animation."""
    key = f"movies:search:{q.lower().strip()}:{page}"

    async def factory() -> dict:
        return await moviebox.search(q.strip(), page)

    try:
        return await cache.get_or_set(key, factory)
    except MovieboxError as exc:
        raise _http_error(exc) from exc


@router.get("/suggest")
async def suggest(q: str = Query(..., min_length=1, max_length=100)):
    """Autocomplete suggestions (titles only — search to deep-link)."""
    key = f"movies:suggest:{q.lower().strip()}"

    async def factory() -> list:
        return await moviebox.suggest(q.strip())

    try:
        return await cache.get_or_set(key, factory)
    except MovieboxError as exc:
        raise _http_error(exc) from exc


@router.get("/{slug}")
async def details(slug: str):
    """Full metadata for one title (movie or series)."""
    key = f"movies:details:{slug}"

    async def factory() -> dict | None:
        return await moviebox.details(slug)

    try:
        data = await cache.get_or_set(key, factory)
    except MovieboxError as exc:
        raise _http_error(exc) from exc
    if not data:
        raise HTTPException(404, f"Title {slug!r} not found on MovieBox")
    return data


@router.get("/{slug}/stream")
async def stream(
    slug: str,
    se: int = Query(0, ge=0, description="Season index for series (0 for movies)"),
    ep: int = Query(0, ge=0, description="1-based episode for series"),
):
    """Direct MP4/HLS sources + captions for one movie (se=0, ep=0) or a
    series episode (se/ep from the detail's `seasons`). Stream URLs are
    signed with an expiry, so results are cached briefly."""
    detail = await details(slug)  # cached; also raises 404
    subject_id = detail.get("id")
    if not subject_id:
        raise HTTPException(404, f"Title {slug!r} has no subject id")
    key = f"movies:stream:{slug}:{se}:{ep}"

    async def factory() -> dict:
        return await moviebox.stream(subject_id, se, ep)

    try:
        data = await short_cache.get_or_set(key, factory)
    except MovieboxError as exc:
        raise _http_error(exc) from exc
    if not data.get("streams"):
        raise HTTPException(
            404,
            detail={"error": "No streams found", "note": data.get("note") or f"{slug} se={se} ep={ep}"},
        )
    return data
