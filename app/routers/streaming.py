"""Streaming endpoints: resolve m3u8 sources for an episode."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from ..cache import short_cache
from ..config import settings
from ..providers.aniraku import AnirakuError, aniraku
from ..providers.anivexa import AnivexaError, anivexa
from ..providers.miruro import CloudflareBlockedError, MiruroError, get_sources
from ..schemas import SourcesResponse

log = logging.getLogger("anidoom.streaming")

router = APIRouter(tags=["streaming"])


@router.get("/watch/{episode_id:path}", response_model=SourcesResponse)
async def watch(episode_id: str):
    """Resolve m3u8 streams/subtitles for an episode id.

    episode_id is a full path, e.g. `watch/anikoto/178005/sub/anikoto-1`
    (from the episodes endpoint). URLs rotate, so results are cached briefly.
    """
    if not episode_id or not episode_id.startswith("watch/"):
        raise HTTPException(
            400,
            "episodeId must look like 'watch/<provider>/<anilistId>/<category>/<ref>'",
        )

    async def factory() -> dict:
        # Dispatch by provider name embedded in the episode id.
        provider = episode_id.split("/")[1] if episode_id.count("/") >= 1 else None
        if not provider:
            raise HTTPException(400, f"Malformed episode id: {episode_id!r}")
        try:
            if provider == "aniraku":
                return await aniraku.get_sources(episode_id)
            if provider in settings.ANIVEXA_PROVIDERS or not settings.MIRURO_ENABLED:
                # Anivexa-sidecar provider. When Miruro is disabled (default)
                # every non-aniraku provider must come from the sidecar, so we
                # can't rely on a static name list — dispatch to Anivexa.
                return await anivexa.get_sources(episode_id)
            return await get_sources(episode_id)  # Miruro provider (only when enabled)
        except (CloudflareBlockedError,) as exc:
            log.warning("Miruro blocked for %s: %s", episode_id, exc)
            raise HTTPException(
                403, detail={"error": "Miruro Cloudflare block", "hint": str(exc)}
            ) from exc
        except (MiruroError, AnivexaError, AnirakuError) as exc:
            raise HTTPException(502, detail=str(exc)) from exc

    data = await short_cache.get_or_set(f"watch:{episode_id}", factory)
    if not data["streams"]:
        raise HTTPException(404, f"No streams found for {episode_id}")
    return SourcesResponse(**data)
