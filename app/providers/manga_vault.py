"""Manga provider — proxies the vendored MangaVault sidecar.

MangaVault (walterwhite-69/MangaVault, MIT) is a self-hosted FastAPI that
aggregates Manganato (nato), Atsumaru (atsu) and Comix (comix). Run it with
`manga-vault/run.bat` (or run.sh) on MANGA_VAULT_URL (default :8001).

Every upstream response is wrapped as {"success", "took", "data"}; this module
normalizes the per-source shapes into unified anidoom shapes.

⚠️ Source status: nato + atsu work against the live sites. The comix source
is currently broken upstream (comix.to changed its API/HTML) — it's disabled
by default via MANGA_VAULT_SOURCES; re-enable it only after updating the
vendored scraper in manga-vault/.
"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote

import httpx

from ..config import settings

log = logging.getLogger("anidoom.manga_vault")

TIMEOUT = 30.0


class MangaVaultError(Exception):
    """Sidecar unreachable or returned an error."""


class MangaVaultNotFoundError(MangaVaultError):
    """Upstream answered 404 / manga not found."""


class MangaVaultProvider:
    name = "manga_vault"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._sem = asyncio.Semaphore(8)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=settings.MANGA_VAULT_URL,
                headers={"Accept": "application/json"},
                timeout=TIMEOUT,
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
                raise MangaVaultError(
                    f"MangaVault unreachable at {settings.MANGA_VAULT_URL} "
                    f"({type(exc).__name__}). Start it with manga-vault/run.bat."
                ) from exc
        if resp.status_code == 404:
            raise MangaVaultNotFoundError(f"MangaVault 404: {path}")
        if resp.status_code >= 400:
            raise MangaVaultError(
                f"MangaVault error {resp.status_code} for {path}: {resp.text[:200]}"
            )
        body = resp.json()
        if isinstance(body, dict) and "data" in body:
            return body["data"]
        return body

    # ── normalization ───────────────────────────────────────────────
    @staticmethod
    def _card(source: str, item: dict) -> dict | None:
        """Unify a per-source home/search item into a MangaCard (None if unusable)."""
        if not isinstance(item, dict):
            return None
        mid = str(item.get("id") or item.get("slug") or "").strip()
        if not mid:
            return None  # can't deep-link without an id
        cover = item.get("cover") or item.get("poster") or ""
        return {
            "source": source,
            "id": mid,
            "title": item.get("title") or "Untitled",
            "cover": cover,
            "latest": item.get("latest")
            or item.get("latest_chapter")
            or item.get("chapter")
            or "",
            "type": item.get("type"),
            "status": item.get("status"),
            "year": item.get("year"),
        }

    def _cards(self, source: str, raw: list) -> list[dict]:
        return [c for c in (self._card(source, it) for it in raw) if c]

    # ── home ────────────────────────────────────────────────────────
    async def home(self) -> list[dict]:
        """Aggregate home sections from every enabled source."""
        sections: list[dict] = []
        endpoints = {
            "nato": "/nato/home",
            "atsu": "/atsu/home",
            "comix": "/comix/home",
        }
        tasks = [
            self._home_section(source, path)
            for source, path in endpoints.items()
            if source in settings.MANGA_VAULT_SOURCES
        ]
        for section in await asyncio.gather(*tasks, return_exceptions=True):
            if isinstance(section, Exception):
                log.warning("Manga home section failed: %s", section)
                continue
            sections.extend(section)
        return sections

    async def _home_section(self, source: str, path: str) -> list[dict]:
        data = await self._call(path)
        sections = []
        if isinstance(data, dict):
            for key, block in data.items():
                if not isinstance(block, dict) or "items" not in block:
                    continue
                items = self._cards(source, block["items"])
                if not items:
                    continue
                sections.append(
                    {
                        "key": f"{source}_{key}",
                        "title": block.get("title") or key.replace("_", " ").title(),
                        "items": items,
                    }
                )
        return sections

    # ── search ──────────────────────────────────────────────────────
    async def search(self, query: str) -> list[dict]:
        """Aggregate search across the enabled sources that support it."""
        items: list[dict] = []
        tasks = []
        if "atsu" in settings.MANGA_VAULT_SOURCES:
            tasks.append(self._search_source("atsu", f"/atsu/search?keyword={quote(query, safe='')}"))
        if "comix" in settings.MANGA_VAULT_SOURCES:
            tasks.append(self._search_source("comix", f"/comix/search?keyword={quote(query, safe='')}"))
        for result in await asyncio.gather(*tasks, return_exceptions=True):
            if isinstance(result, Exception):
                log.warning("Manga search source failed: %s", result)
                continue
            items.extend(result)
        return items

    async def _search_source(self, source: str, path: str) -> list[dict]:
        data = await self._call(path)
        raw = []
        if isinstance(data, dict):
            raw = data.get("items") or []
            if not raw and isinstance(data.get("result"), dict):
                raw = data["result"].get("items") or []
            if data.get("error"):
                log.warning("Manga %s search upstream error: %s", source, data["error"])
                raw = []
        elif isinstance(data, list):
            raw = data
        return self._cards(source, raw)

    # ── details ─────────────────────────────────────────────────────
    async def details(self, source: str, media_id: str) -> dict | None:
        path = f"/{source}/manga/{quote(media_id, safe='')}/details"
        data = await self._call(path)
        if not isinstance(data, dict) or data.get("error"):
            return None
        return self._normalize_details(source, media_id, data)

    def _normalize_details(self, source: str, media_id: str, data: dict) -> dict:
        chapters = []
        for c in data.get("chapters", []):
            if not isinstance(c, dict):
                continue
            image_path = self._chapter_path(source, media_id, c)
            if not image_path:
                continue  # can't read without a resolvable page path
            chapters.append(
                {
                    "id": str(c.get("id") or c.get("chapter_slug") or c.get("chapter_id") or ""),
                    "number": c.get("number"),
                    "title": c.get("title") or c.get("chapter_name") or "",
                    "imagePath": image_path,
                }
            )
        return {
            "source": source,
            "id": media_id,
            "title": data.get("title") or "Untitled",
            "cover": data.get("cover") or data.get("image") or "",
            "description": data.get("description") or "",
            "authors": data.get("authors") or "",
            "status": data.get("status") or data.get("type") or "",
            "genres": data.get("genres") or [],
            "views": str(data.get("views") or ""),
            "updated": data.get("updated") or data.get("updated_at") or "",
            "chapters": chapters,
        }

    def _chapter_path(self, source: str, media_id: str, chapter: dict) -> str:
        # Prefer the relative path manga-vault already provides (atsu/comix
        # chapters include a `url`); only nato builds it from slugs.
        url = chapter.get("url") or ""
        if url:
            return f"/{url.lstrip('/')}"
        if source == "nato":
            cid = chapter.get("id") or chapter.get("chapter_slug") or ""
            return f"/nato/manga/{quote(media_id, safe='')}/{quote(cid, safe='')}/images"
        return ""

    # ── chapter pages ───────────────────────────────────────────────
    async def chapter_images(self, path: str) -> list[str]:
        """Proxy manga-vault's chapter-images endpoint (relative `path`)."""
        if not path.startswith("/"):
            raise MangaVaultError(f"Bad chapter path: {path!r}")
        data = await self._call(path)
        if isinstance(data, dict):
            if data.get("error"):
                raise MangaVaultError(str(data.get("error")))
            data = []
        return [str(u) for u in data]


manga_vault = MangaVaultProvider()
