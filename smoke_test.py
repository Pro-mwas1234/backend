"""Smoke test for the anidoom backend.

Usage (from backend/, with the venv active):
    python smoke_test.py            # endpoint suite (TestClient + lifespan)
    python smoke_test.py providers  # direct provider calls in one event loop

Requires network access to AniList / Jikan.
"""
from __future__ import annotations

import asyncio
import sys

from fastapi.testclient import TestClient

from app.main import app
from app.providers.anilist import anilist
from app.providers.mal import jikan


async def provider_loop() -> None:
    """Exercise providers directly inside a single explicit event loop."""
    t = await anilist.trending(1, 3)
    print("TREND_OK", len(t["results"]))
    d = await anilist.details(21)
    print("DETAIL_OK", d and d["title"]["romaji"])
    m = await jikan.enrich(20)
    print("MAL_OK", bool(m and m.get("url")))
    await anilist.close()
    await jikan.close()


def manga_suite(c) -> None:
    """Manga endpoints (require the MangaVault sidecar on :8001)."""
    r = c.get("/api/manga/home")
    print("MANGA_HOME", r.status_code, "sections:", len(r.json()) if r.status_code == 200 else r.text[:120])
    if r.status_code != 200:
        print("MANGA_SKIPPED (sidecar down?)")
        return

    sections = r.json()

    r = c.get("/api/manga/search?q=one+piece")
    print("MANGA_SEARCH", r.status_code, "items:", len(r.json()) if r.status_code == 200 else r.text[:100])

    first = None
    for section in sections:
        if section.get("items"):
            first = section["items"][0]
            break
    if not first:
        print("MANGA_SKIPPED (no items)")
        return

    r = c.get(f"/api/manga/{first['source']}/{first['id']}/details")
    j = r.json() if r.status_code == 200 else {}
    print("MANGA_DETAILS", r.status_code, j.get("title", "")[:60], "chapters:", len(j.get("chapters", [])))

    chapters = j.get("chapters") or []
    if chapters:
        from urllib.parse import quote
        path = chapters[-1]["imagePath"]
        r = c.get(f"/api/manga/chapter-images?path={quote(path, safe='')}")
        pages = r.json() if r.status_code == 200 else []
        print("MANGA_IMAGES", r.status_code, "pages:", len(pages))
        if pages:
            r = c.get(f"/api/manga/img?url={quote(pages[0], safe='')}&src={first['source']}")
            print("MANGA_IMG", r.status_code, r.headers.get("content-type"))


def movie_suite(c) -> None:
    """Movies endpoints (require the Moviebox-API sidecar on :8003)."""
    r = c.get("/api/movies/home")
    print("MOVIE_HOME", r.status_code, "sections:", len(r.json()) if r.status_code == 200 else r.text[:120])
    if r.status_code != 200:
        print("MOVIE_SKIPPED (sidecar down?)")
        return

    r = c.get("/api/movies/catalog?type=movie&page=1")
    j = r.json() if r.status_code == 200 else {}
    print("MOVIE_CATALOG", r.status_code, "results:", len(j.get("results", [])))

    r = c.get("/api/movies/search?q=dune")
    j = r.json() if r.status_code == 200 else {}
    print("MOVIE_SEARCH", r.status_code, "results:", len(j.get("results", [])))

    r = c.get("/api/movies/suggest?q=dun")
    print("MOVIE_SUGGEST", r.status_code, "items:", len(r.json()) if r.status_code == 200 else r.text[:100])

    slug = (j.get("results") or [{}])[0].get("slug") if r.status_code == 200 else None
    if not slug:
        print("MOVIE_SKIPPED (no search slug)")
        return

    r = c.get(f"/api/movies/{slug}")
    d = r.json() if r.status_code == 200 else {}
    print("MOVIE_DETAILS", r.status_code, d.get("title", "")[:60], "| type:", d.get("type"))

    r = c.get(f"/api/movies/{slug}/stream")
    j = r.json() if r.status_code == 200 else {}
    print(
        "MOVIE_STREAM", r.status_code,
        "streams:", len(j.get("streams", [])),
        "captions:", len(j.get("captions", [])),
    )


def endpoint_suite() -> None:
    """Full API suite. `with TestClient(app)` keeps one event loop + lifespan."""
    with TestClient(app) as c:
        manga_suite(c)
        movie_suite(c)
        r = c.get("/api/health")
        print("HEALTH", r.status_code)

        r = c.get("/api/anime/trending")
        print("TRENDING", r.status_code, len(r.json().get("results", [])))

        r = c.get("/api/anime/21")
        j = r.json()
        print(
            "DETAILS", r.status_code,
            (j.get("title") or {}).get("romaji") if r.status_code == 200 else r.text[:150],
        )

        r = c.get("/api/anime/21/mal")
        print(
            "MAL", r.status_code,
            (r.json().get("url") or "") if r.status_code == 200 else r.text[:120],
        )

        r = c.get("/api/anime/search?q=frieren&mal=1")
        print(
            "SEARCH", r.status_code,
            len(r.json().get("results", [])) if r.status_code == 200 else r.text[:120],
        )

        # Proxy a REAL cover URL pulled from the details response above.
        r = c.get("/api/anime/21")
        cover = (r.json().get("coverImage") or "") if r.status_code == 200 else ""
        from urllib.parse import quote

        if cover:
            r = c.get(f"/api/img?url={quote(cover, safe='')}")
            print("IMG", r.status_code, r.headers.get("content-type"), len(r.content), "bytes")
        else:
            print("IMG skipped (no cover)")


if __name__ == "__main__":
    if "providers" in sys.argv:
        asyncio.run(provider_loop())
    else:
        endpoint_suite()
