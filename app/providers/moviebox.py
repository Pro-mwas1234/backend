"""Moviebox provider — proxies the vendored DavidCyril1/moviebox-api sidecar.

The Movies & TV section is served by a vendored copy of
[`DavidCyril1/moviebox-api`](https://github.com/DavidCyril1/moviebox-api)
(lives in `moviebox-api/`, run on `MOVIEBOX_URL`, default `:8003`). It is an
Express (Node) server that talks to MovieBox.ph's mobile BFF
(`h5.aoneroom.com/wefeed-h5-bff`) with an app-like session, and exposes:

- `/api/homepage`         home rows (operatingList)
- `/api/trending`         trending (paginated)
- `/api/info/:movieId`    full metadata (subject + resource + stars)
- `/api/sources/:movieId` downloads/streams + captions, with
  `streamUrl` pointing at its own zero-buffer `/api/stream?url=` proxy
- `/api/search/:query`    search (patched to MovieBox's web BFF — the mobile
  BFF search returns empty)
- `/api/catalog?type=&page=&sort=`   paged, sortable catalogs (patched)
- `/api/suggest?q=`       autocomplete (patched)
- `/api/detail/:slug`     metadata by slug (patched)
- `/api/stream?url=`      video proxy with Range support

Why the sidecar's own stream proxy? MovieBox's CDN rate-limits datacenter
egress (Cloudflare 429s) and rejects bare-browser requests — the sidecar
fetches the signed MP4 with frontend-mirror Referer/Origin headers from the
residential IP and pipes bytes back with Range support, which the browser can
play directly. The player therefore streams via the sidecar URL, not the CDN.

This module normalizes the sidecar's shapes into unified anidoom shapes:
- home:    { sections: [{ key, title, items: [cards] }] }
- catalog: { page, perPage, total, hasNextPage, results: [cards] }
- search:  same Page shape
- suggest: { suggestions: [plain strings] }
- detail:  unified movie/series metadata (id, seasons, cast, …)
- stream:  { subjectId, se, ep, hasResource, note, limited, freeEpisodes,
             streams: [{ url, resolution, format, size, type, direct }],
             captions: [{ lang, label, url }] }
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from ..config import settings

log = logging.getLogger("anidoom.moviebox")

TIMEOUT = 60.0

_TAB_TYPE = {2: "movie", 5: "tv", 8: "animation"}


class MovieboxError(Exception):
    """Sidecar unreachable or returned an error."""


class MovieboxNotFoundError(MovieboxError):
    """Sidecar answered 404 / title not found / no streams for selection."""


class MovieboxProvider:
    name = "moviebox"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._sem = asyncio.Semaphore(6)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=settings.MOVIEBOX_URL,
                headers={"Accept": "application/json"},
                # Short connect timeout: an unreachable sidecar should fail fast
                # (502) instead of hanging the request for the full read timeout.
                timeout=httpx.Timeout(TIMEOUT, connect=5.0),
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _call(self, path: str) -> dict | list:
        client = await self._get_client()
        async with self._sem:
            try:
                resp = await client.get(path)
            except httpx.HTTPError as exc:
                raise MovieboxError(
                    f"Moviebox sidecar unreachable at {settings.MOVIEBOX_URL} "
                    f"({type(exc).__name__}). Run moviebox-api/ (npm install, "
                    f"node index.js) or set MOVIEBOX_URL."
                ) from exc
        if resp.status_code == 404:
            raise MovieboxNotFoundError(f"Moviebox 404: {path}")
        if resp.status_code >= 400:
            raise MovieboxError(
                f"Moviebox error {resp.status_code} for {path}: {resp.text[:200]}"
            )
        body = resp.json()
        if isinstance(body, dict) and body.get("status") == "error":
            raise MovieboxError(f"Moviebox error for {path}: {body.get('message') or body.get('error')}")
        return body.get("data", body) if isinstance(body, dict) else body

    # ── normalization ───────────────────────────────────────────────
    @staticmethod
    def _card(item: dict) -> dict | None:
        """Unify a sidecar home/catalog/search item into a movie card."""
        slug = str(item.get("detailPath") or item.get("slug") or "").strip()
        if not slug:
            return None  # can't deep-link without a slug
        cover = (item.get("cover") or {})
        cover_url = cover.get("url") if isinstance(cover, dict) else None
        upcoming = _is_upcoming(item.get("releaseDate"), item.get("appointmentDate"))
        return {
            "id": str(item.get("subjectId") or item.get("id") or ""),
            "slug": slug,
            "title": item.get("title") or item.get("name") or "Untitled",
            "cover": item.get("thumbnail") or cover_url or "",
            "rating": item.get("imdbRatingValue") or item.get("rating"),
            "year": str(item.get("releaseDate") or "")[:4] or item.get("year"),
            # "Coming Soon" wins over the corner language tag (English/Hindi)
            "badge": "Coming Soon" if upcoming else (item.get("corner") or item.get("badge") or ""),
            "upcoming": upcoming,
        }

    @staticmethod
    def _cards(raw: list) -> list[dict]:
        return [
            c
            for it in raw
            if isinstance(it, dict)
            for c in [MovieboxProvider._card(it)]
            if c
        ]

    # ── home ────────────────────────────────────────────────────────
    async def home(self) -> list[dict]:
        data = await self._call("/api/homepage")
        sections = []
        for op in data.get("operatingList", []) or []:
            op_type = op.get("type")
            title = op.get("title") or "Featured"
            if op_type == "BANNER":
                items = [
                    {
                        "name": it.get("title"),
                        "poster_url": (it.get("image") or {}).get("url")
                        or (it.get("subject") or {}).get("cover", {}).get("url"),
                        "slug": it.get("detailPath")
                        or (it.get("subject") or {}).get("detailPath"),
                        "subject_id": (it.get("subject") or {}).get("subjectId"),
                        "badge": (it.get("subject") or {}).get("corner"),
                    }
                    for it in (op.get("banner") or {}).get("items", [])
                    if it.get("title") and "Communities" not in it.get("title")
                ]
            # APPOINTMENT_LIST = MovieBox's "Upcoming Calendar" row (future
            # premieres) — surface it so upcoming titles show with badges.
            elif op_type.startswith("SUBJECTS") or op_type in ("CUSTOM", "APPOINTMENT_LIST"):
                items = op.get("subjects") or []
            else:
                continue
            cards = self._cards(items)
            if not cards:
                continue
            sections.append(
                {
                    "key": title.replace(" ", "_").lower(),
                    "title": title,
                    "items": cards,
                }
            )
        return sections

    # ── catalog / search ────────────────────────────────────────────
    async def catalog(self, tab_id: int, page: int, sort: str) -> dict:
        """Paged catalog for a tab (2=movie, 5=tv, 8=animation)."""
        mtype = _TAB_TYPE.get(tab_id, "movie")
        data = await self._call(f"/api/catalog?type={mtype}&page={page}&sort={sort}")
        return self._page(data, page)

    async def search(self, query: str, page: int = 1) -> dict:
        data = await self._call(f"/api/search/{_q(query)}?page={page}&perPage=24")
        return self._page(data, page)

    async def suggest(self, query: str) -> list[dict]:
        data = await self._call(f"/api/suggest?q={_q(query)}")
        # The sidecar returns plain strings (no slug/id) — the Movies page
        # matches suggestions by title, so a search navigation still works.
        return [
            {"title": word, "slug": None, "id": None}
            for word in data.get("suggestions", [])
            if isinstance(word, str) and word.strip()
        ]

    @staticmethod
    def _page(data: dict, page: int) -> dict:
        """Unify a sidecar catalog/search payload into the anidoom Page shape."""
        if not isinstance(data, dict):
            data = {}
        raw_items = data.get("items") or data.get("movies") or []
        total = data.get("total") or data.get("count") or len(raw_items)
        has_next = bool(data.get("hasNextPage") or data.get("has_more"))
        if not has_next:
            per_page = data.get("perPage") or 24
            has_next = len(raw_items) >= per_page
        return {
            "page": data.get("page") or page,
            "perPage": data.get("perPage") or 24,
            "total": total,
            "hasNextPage": has_next,
            "results": MovieboxProvider._cards(raw_items),
        }

    # ── details ─────────────────────────────────────────────────────
    async def details(self, slug: str) -> dict | None:
        data = await self._call(f"/api/detail/{_q(slug)}")
        subject = data.get("subject") if isinstance(data, dict) else None
        if not isinstance(subject, dict) or not subject.get("title"):
            return None
        cover = (subject.get("cover") or {})
        cover_url = cover.get("url") if isinstance(cover, dict) else ""
        release = subject.get("releaseDate") or ""
        cast = [
            {
                "name": s.get("name") or s.get("personName") or "",
                "character": s.get("character") or s.get("role") or "",
                "avatar": s.get("avatarUrl") or (s.get("avatar") or {}).get("url") or "",
            }
            for s in data.get("stars") or []
            if isinstance(s, dict) and (s.get("name") or s.get("personName"))
        ]
        # Movies carry a placeholder season {se: 0, maxEp: 0} — drop it so
        # only real seasons (se >= 1 with episodes) reach the frontend.
        seasons = [
            {"se": s.get("se"), "maxEp": s.get("maxEp")}
            for s in (data.get("resource") or {}).get("seasons") or []
            if isinstance(s, dict)
            and s.get("se") is not None
            and (s.get("se") > 0 or (s.get("maxEp") or 0) > 0)
        ]
        trailer = subject.get("trailer")
        return {
            "id": str(subject.get("subjectId") or ""),
            "slug": slug,
            "title": subject.get("title") or "Untitled",
            "description": subject.get("description") or "",
            "cover": cover_url,
            "banner": cover_url,
            "genres": [g.strip() for g in (subject.get("genre") or "").split(",") if g.strip()],
            "country": subject.get("countryName") or "",
            "rating": subject.get("imdbRatingValue") or "",
            "imdbCount": None,
            "releaseDate": release,
            "year": release[:4] if release else "",
            "duration": _to_minutes(subject.get("duration")),
            "type": 2 if seasons else 1,  # sidecar subjectType exists, but seasons are reliable
            "season": None,
            "audioTracks": subject.get("dubs") or [],
            "hasResource": bool(subject.get("hasResource")),
            "trailer": trailer.get("url") if isinstance(trailer, dict) else (trailer or ""),
            "cast": cast,
            "seasons": seasons,
            "corner": subject.get("corner") or "",
        }

    # ── streaming ───────────────────────────────────────────────────
    async def stream(self, subject_id: str, se: int = 0, ep: int = 0) -> dict:
        """Signed MP4 sources + captions via the sidecar's stream proxy."""
        try:
            data = await self._call(
                f"/api/sources/{_q(subject_id)}?season={se}&episode={ep}"
            )
        except MovieboxNotFoundError:
            return {
                "subjectId": str(subject_id),
                "se": se,
                "ep": ep,
                "hasResource": False,
                "note": "No stream found for this selection.",
                "freeEpisodes": None,
                "limited": False,
                "streams": [],
                "captions": [],
            }
        if not isinstance(data, dict):
            data = {}
        # Sidecar streamUrl already points at its own /api/stream proxy, which
        # fetches the signed CDN URL with frontend-mirror referers — the player
        # plays it directly (no HLS worker hop needed).
        processed = data.get("processedSources") or []
        streams = []
        for s in processed:
            if not isinstance(s, dict):
                continue
            direct = s.get("directUrl") or s.get("url")
            if not direct:
                continue
            # Prefer the sidecar's own streamUrl (protocol-aware via
            # x-forwarded-proto, so an HTTPS frontend never gets a mixed-content
            # http:// URL); fall back to rebuilding from the configured base.
            proxy_url = s.get("streamUrl") or f"{settings.MOVIEBOX_URL}/api/stream?url={_q(direct)}"
            download_url = s.get("downloadUrl") or (
                f"{settings.MOVIEBOX_URL}/api/download?url={_q(direct)}"
            )
            streams.append(
                {
                    "url": proxy_url,
                    # Sidecar /api/download proxy — resumable, sets an
                    # attachment Content-Disposition (title_SE_quality.mp4).
                    "downloadUrl": download_url,
                    "resolution": f"{s.get('quality')}p" if s.get("quality") else "HD",
                    "format": s.get("format") or "MP4",
                    "size": s.get("size"),
                    "duration": None,
                    "type": "mp4",
                    "direct": True,  # already proxied by the sidecar
                }
            )
        # highest quality first
        streams.sort(
            key=lambda x: int(str(x["resolution"]).rstrip("p")) if str(x["resolution"]).rstrip("p").isdigit() else 0,
            reverse=True,
        )
        captions = [
            {
                "lang": c.get("lan") or "",
                "label": c.get("lanName") or c.get("lan") or "",
                "url": c.get("url") or "",
            }
            for c in data.get("captions") or []
            if isinstance(c, dict) and c.get("url")
            and (
                c.get("lan") == "en"
                or str(c.get("lanName") or "").lower().find("english") >= 0
            )
        ]
        return {
            "subjectId": str(subject_id),
            "se": se,
            "ep": ep,
            "hasResource": bool(streams),
            "note": None if streams else (data.get("note") or "No stream found for this selection."),
            "freeEpisodes": data.get("freeNum"),
            "limited": bool(data.get("limited")),
            "streams": streams,
            "captions": captions,
        }


def _q(value) -> str:
    from urllib.parse import quote

    return quote(str(value), safe="")


def _is_upcoming(release_date, appointment_date=None) -> bool:
    """True when a title isn't released yet: a future `releaseDate` (MovieBox
    sets it before the premiere) or a pending `appointmentDate`."""
    from datetime import date

    today = date.today()
    for raw in (release_date, appointment_date):
        if not raw:
            continue
        try:
            dt = date.fromisoformat(str(raw)[:10])
        except (TypeError, ValueError):
            continue
        if dt > today:
            return True
    return False


def _to_minutes(seconds) -> int | None:
    if not seconds:
        return None
    try:
        return round(int(seconds) / 60)
    except (TypeError, ValueError):
        return None


moviebox = MovieboxProvider()
