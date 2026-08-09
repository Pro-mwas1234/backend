"""Manga catalog + reader endpoints (backed by the MangaVault sidecar)."""
from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from ..cache import cache, short_cache
from ..config import settings
from ..providers.manga_vault import (
    MangaVaultError,
    MangaVaultNotFoundError,
    manga_vault,
)

log = logging.getLogger("anidoom.manga")

# Literal routes are declared before the parameterized one.
router = APIRouter(prefix="/manga", tags=["manga"])

_SOURCES = ("nato", "atsu", "comix")


def _http_error(exc: MangaVaultError) -> HTTPException:
    log.warning("MangaVault error: %s", exc)
    if isinstance(exc, MangaVaultNotFoundError):
        return HTTPException(404, detail=str(exc))
    return HTTPException(503, detail=str(exc))


@router.get("/home")
async def home():
    """Aggregated trending/latest sections from all enabled sources."""
    try:
        return await cache.get_or_set("manga:home", manga_vault.home)
    except MangaVaultError as exc:
        raise _http_error(exc) from exc


@router.get("/search")
async def search(q: str = Query(..., min_length=1, max_length=200)):
    """Search manga across the enabled sources that support it (atsu, comix)."""
    key = f"manga:search:{q.lower().strip()}"

    async def factory() -> list:
        return await manga_vault.search(q.strip())

    try:
        return await cache.get_or_set(key, factory)
    except MangaVaultError as exc:
        raise _http_error(exc) from exc


@router.get("/chapter-images")
async def chapter_images(
    path: str = Query(..., min_length=1, max_length=500, description="Relative manga-vault path from the chapter's imagePath")
):
    """Page image URLs for a chapter (proxied from the sidecar)."""
    key = f"manga:chapter:{path}"

    async def factory() -> list[str]:
        return await manga_vault.chapter_images(path)

    try:
        return await short_cache.get_or_set(key, factory)
    except MangaVaultError as exc:
        raise _http_error(exc) from exc


@router.get("/img")
@router.get("/image-proxy")
async def manga_img(
    url: str = Query(..., max_length=1000, description="Chapter page image URL"),
    src: str = Query("", max_length=20, description="Source (nato/atsu/comix) for the Referer header"),
    referer: str = Query("", max_length=500, description="Custom referer header"),
):
    """Proxy manga page images with the right Referer (hotlink protection).

    MANGA_IMG_ALLOW in .env restricts allowed hosts; empty = any http(s) URL
    (fine for a self-hosted service — set MANGA_IMG_ALLOW before exposing
    anidoom publicly, otherwise this is an open proxy).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(400, "url must be http(s)")

    def host_allowed(host: str) -> bool:
        return not settings.MANGA_IMG_ALLOW or host in settings.MANGA_IMG_ALLOW

    if not host_allowed(parsed.hostname):
        raise HTTPException(403, f"Host not allowed: {parsed.hostname}")

    # Use custom referer if provided, otherwise use source-based referer
    if referer:
        final_referer = referer
    else:
        final_referer = settings.MANGA_REFERERS.get(src, settings.MANGA_REFERERS["nato"])

    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                    ),
                    "Referer": final_referer,
                    "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
                },
            )
            # Redirects must not bypass the allow-list.
            final_host = resp.url.host if resp.url else parsed.hostname
            if not host_allowed(final_host):
                raise HTTPException(403, f"Redirected to disallowed host: {final_host}")
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        log.warning("manga img proxy failed for %s: %s", url, exc)
        raise HTTPException(502, "Image fetch failed") from exc
    if resp.status_code != 200:
        raise HTTPException(502, f"Image fetch returned {resp.status_code}")
    return Response(
        content=resp.content,
        media_type=resp.headers.get("content-type", "image/jpeg"),
        headers={
            "Cache-Control": "public, max-age=86400",
            "Access-Control-Allow-Origin": "*",
        },
    )


@router.get("/{source}/{media_id}/details")
async def details(source: str, media_id: str):
    """Full metadata + chapter list for one manga."""
    if source not in _SOURCES:
        raise HTTPException(400, f"source must be one of {_SOURCES}")
    key = f"manga:details:{source}:{media_id}"

    async def factory() -> dict | None:
        return await manga_vault.details(source, media_id)

    try:
        data = await cache.get_or_set(key, factory)
    except MangaVaultError as exc:
        raise _http_error(exc) from exc
    if not data:
        raise HTTPException(404, f"Manga {media_id!r} not found on {source}")
    return data
