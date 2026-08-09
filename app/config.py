"""Central configuration, loaded from environment variables / backend/.env."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load backend/.env relative to this file
_BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(_BACKEND_DIR / ".env")


def _list_env(name: str, default: str) -> list[str]:
    return [p.strip() for p in os.getenv(name, default).split(",") if p.strip()]


def _url_env(name: str, default: str) -> str:
    """Env var holding a service base URL.

    Render Blueprint ``fromService`` refs arrive scheme-less
    (e.g. ``manga-vault:8001`` over the private network) — normalize to
    http:// so httpx base_url stays valid.
    """
    url = os.getenv(name, default).strip()
    if url and not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    return url


class Settings:
    """Runtime settings. Attributes mirror backend/.env.example."""

    # Miruro pipe — DISABLED by default: upstream now returns 403 on every
    # pipe call even with a cf_clearance cookie (expired/mismatched binding).
    # Streaming defaults to Anivexa (anivexa-api sidecar) + Aniraku fallback.
    # To re-enable, re-mint cf_clearance (docs/CLOUDFLARE.md) and set
    # MIRURO_ENABLED=true.
    MIRURO_ENABLED: bool = os.getenv("MIRURO_ENABLED", "false").lower() == "true"
    MIRURO_DOMAINS: list[str] = _list_env(
        "MIRURO_DOMAINS", "www.miruro.tv,miruro.to,miruro.ru,miruro.bz"
    )
    CF_CLEARANCE: str = os.getenv("CF_CLEARANCE", "").strip()
    CF_USER_AGENT: str = os.getenv(
        "CF_USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
    )

    # Metadata providers
    ANILIST_URL: str = os.getenv("ANILIST_URL", "https://graphql.anilist.co")
    JIKAN_URL: str = os.getenv("JIKAN_URL", "https://api.jikan.moe/v4")

    # Manga sidecar (vendored walterwhite-69/MangaVault, see manga-vault/)
    MANGA_VAULT_URL: str = _url_env("MANGA_VAULT_URL", "http://127.0.0.1:8001")
    # nato (Manganato) + atsu (Atsumaru) work; comix is broken upstream and
    # disabled by default (comix.to changed its API) — see docs/DATA_SOURCES.md.
    MANGA_VAULT_SOURCES: list[str] = _list_env("MANGA_VAULT_SOURCES", "nato,atsu")

    # Streaming sidecar (vendored walterwhite-69/Anivexa-API, see anivexa-api/)
    ANIVEXA_URL: str = _url_env("ANIVEXA_URL", "http://127.0.0.1:8002")
    # Aniraku hosted fallback (aniraku.tech's own backend — resolves streams
    # server-side, handling Cloudflare on its side)
    ANIRAKU_URL: str = _url_env(
        "ANIRAKU_URL", "https://aniraku-backend-fhyy.onrender.com/api/v1"
    )
    ANIRAKU_ENABLED: bool = os.getenv("ANIRAKU_ENABLED", "true").lower() == "true"
    # Movies provider — vendored DavidCyril1/moviebox-api Express sidecar
    # (see moviebox-api/). Run on :8003 with `node index.js`. Its own
    # /api/stream proxy fetches signed MP4s with frontend-mirror referers, so
    # streams play even though MovieBox's CDN rate-limits datacenter egress
    # (a Cloudflare Worker cannot serve movies — 429 RESOURCE_EXHAUSTED).
    MOVIEBOX_URL: str = _url_env("MOVIEBOX_URL", "http://127.0.0.1:8003")
    # Poster hosts (pbcdnw/pacdn.aoneroom.com) are covered by IMG_PROXY_ALLOW.
    # Providers Anivexa-API exposes (used to dispatch /watch routes)
    ANIVEXA_PROVIDERS: set[str] = {
        "allmanga", "reanime", "anikoto", "animegg", "anineko",
        "anidbapp", "2dhive", "animenosub", "anizone", "anibd",
        "senshi", "kaa", "animedunya",
    }
    # Empty = allow any http(s) URL through /api/manga/img (self-hosted use).
    MANGA_IMG_ALLOW: list[str] = _list_env("MANGA_IMG_ALLOW", "")

    # Auth — local accounts (SQLite) + AniList OAuth. Create an AniList API
    # client at https://anilist.co/settings/developer to enable that button.
    ANILIST_CLIENT_ID: str = os.getenv("ANILIST_CLIENT_ID", "").strip()
    ANILIST_CLIENT_SECRET: str = os.getenv("ANILIST_CLIENT_SECRET", "").strip()
    # Where the OAuth callback bounces the user back to after sign-in.
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://127.0.0.1:5175").rstrip("/")
    # SQLite file for users/sessions (created automatically).
    AUTH_DB_PATH: str = os.getenv(
        "AUTH_DB_PATH", str(Path(__file__).resolve().parent.parent / "anidoom.db")
    )

    # App
    CACHE_TTL: int = int(os.getenv("CACHE_TTL", "600"))
    CORS_ORIGINS: list[str] = _list_env("CORS_ORIGINS", "*")
    IMG_PROXY_ALLOW: list[str] = _list_env(
        "IMG_PROXY_ALLOW",
        "s4.anilist.co,cdn.myanimelist.net,pbcdnw.aoneroom.com,pacdn.aoneroom.com",
    )
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

    # Miruro pipe version sent in every request payload
    PIPE_VERSION: str = "0.1.0"

    # Referer sent when proxying manga chapter images (per source site)
    MANGA_REFERERS: dict[str, str] = {
        "nato": "https://www.manganato.gg/",
        "atsu": "https://atsu.moe/",
        "comix": "https://comix.to/",
    }


settings = Settings()
