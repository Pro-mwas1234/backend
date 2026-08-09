"""Health + image proxy endpoints."""
from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from ..config import settings
from ..schemas import Health

log = logging.getLogger("anidoom.health")

router = APIRouter(tags=["meta"])


@router.get("/health", response_model=Health)
async def health() -> Health:
    return Health(
        status="ok",
        miruro_enabled=settings.MIRURO_ENABLED,
        miruro_clearance=bool(settings.CF_CLEARANCE),
        anivexa_url=settings.ANIVEXA_URL,
        aniraku_enabled=settings.ANIRAKU_ENABLED,
        moviebox_url=settings.MOVIEBOX_URL,
    )


@router.get("/activity/ping")
async def activity_ping_get():
    """Hugging Face Space activity ping endpoint."""
    return {"status": "ok"}


@router.post("/activity/ping")
async def activity_ping_post():
    """Hugging Face Space activity ping endpoint."""
    return {"status": "ok"}


@router.get("/notifications/upcoming")
async def upcoming_notifications():
    """Hugging Face Space notifications endpoint."""
    return {"notifications": []}


@router.get("/img")
async def img_proxy(url: str = Query(..., description="Absolute image URL")):
    """Proxy posters/banners to dodge hotlink/referer blocks.

    Only hosts in IMG_PROXY_ALLOW are fetched.
    """
    host = urlparse(url).hostname
    if not host or host not in settings.IMG_PROXY_ALLOW:
        raise HTTPException(400, f"Host not allowed: {host}")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        log.warning("img proxy failed for %s: %s", url, exc)
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
