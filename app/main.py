"""anidoom API — FastAPI application."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .config import settings
from .providers.anilist import anilist
from .providers.aniraku import aniraku
from .providers.anivexa import anivexa
from .providers.mal import jikan
from .providers.manga_vault import manga_vault
from .providers.moviebox import moviebox
from .routers import anime, auth, health, manga, movies, streaming

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("anidoom")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Providers create their HTTP clients lazily on first use (inside this
    # loop); close them cleanly on shutdown.
    yield
    await anilist.close()
    await jikan.close()
    await manga_vault.close()
    await moviebox.close()
    await anivexa.close()
    await aniraku.close()


app = FastAPI(
    title="anidoom API",
    version="0.1.0",
    description=(
        "Anime metadata (AniList + MAL/Jikan) and m3u8 stream resolution "
        "(Anivexa sidecar primary, Aniraku fallback). See docs/API.md."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_PREFIX = "/api"
app.include_router(health.router, prefix=API_PREFIX)
app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(anime.router, prefix=API_PREFIX)
app.include_router(streaming.router, prefix=API_PREFIX)
app.include_router(manga.router, prefix=API_PREFIX)
app.include_router(movies.router, prefix=API_PREFIX)

# Mount static frontend files if available (must be after API routes for proper routing)
static_dir = Path("/app/static")
if static_dir.exists():
    # Mount static files for assets
    app.mount("/assets", StaticFiles(directory=str(static_dir / "assets")), name="assets")
    
    # SPA catch-all: serve index.html for any non-API, non-asset route
    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_catch_all(full_path: str):
        """Serve index.html for SPA routing (catch-all for non-API routes)."""
        # Don't intercept API routes or assets
        if full_path.startswith("api/") or full_path.startswith("assets/"):
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": "Not found"}, status_code=404)
        
        index_file = static_dir / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return {"error": "Frontend not built"}
    
    # Also handle root explicitly
    @app.get("/", include_in_schema=False)
    async def root_spa():
        """Serve index.html for root route."""
        index_file = static_dir / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return {"error": "Frontend not built"}


@app.get("/api", include_in_schema=False)
async def root() -> dict:
    return {
        "name": "anidoom API",
        "docs": "/docs",
        "health": f"{API_PREFIX}/health",
        "miruro_enabled": settings.MIRURO_ENABLED,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
