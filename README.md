# anidoom Backend

Python FastAPI backend for the anidoom media platform. This service provides metadata, authentication, and streaming endpoints for anime, manga, and movie content, while also serving as the API layer for the frontend application.

## Overview

The backend is organized around a FastAPI application in `app/main.py` and exposes REST endpoints under the `/api` prefix. It combines data from external metadata providers and optional local sidecars for stream resolution and media serving.

Core responsibilities include:

- Anime search, discovery, and detail APIs
- Manga metadata and image access
- Movies and streaming discovery
- Local account authentication and AniList OAuth
- Health checks and image proxying
- Cached metadata responses for performance
- Optional static frontend serving when bundled assets are present

## Tech Stack

- Python 3
- FastAPI
- Uvicorn
- httpx
- python-dotenv
- SQLite for local auth persistence
- Optional provider-sidecars for streaming and manga data

## Repository Structure

```text
.
├── app/
│   ├── cloudflare/
│   ├── providers/
│   ├── routers/
│   ├── services/
│   ├── __init__.py
│   ├── cache.py
│   ├── config.py
│   ├── main.py
│   ├── schemas.py
│   └── ...
├── Dockerfile
├── README.md
├── requirements.txt
├── run.py
├── smoke_test.py
├── anidoom.db
└── backend-8000.err.log
```

### Key modules

- `app/main.py` – FastAPI app setup, middleware, router registration, and static frontend mount
- `app/config.py` – environment-driven runtime settings and service URLs
- `app/routers/` – API endpoints for health, auth, anime, manga, movies, and streaming
- `app/services/catalog.py` – unified catalog service with provider merging and caching
- `app/providers/` – integrations with AniList, Jikan, MangaVault, Anivexa, Aniraku, and MovieBox
- `app/cloudflare/clearance.py` – Cloudflare-related logic for browser clearance handling

## Features

### Metadata and discovery

The backend retrieves metadata from AniList and enriches it with Jikan/MAL data where applicable. Catalog workflows support:

- Search and browse endpoints
- Trending / popular / recent / upcoming collections
- Media detail responses
- Poster/banner image proxying

### Streaming

Streaming support is routed through provider sidecars and fallback logic:

- Anivexa sidecar as the primary stream source
- Aniraku as a fallback provider
- Miruro support behind configuration flags
- Frontend-friendly stream metadata and watch endpoints

### Manga support

Manga-related functionality is powered by a MangaVault sidecar and associated source settings, with image and metadata resolution tailored to provider requirements.

### Authentication

The app includes:

- Local username/email registration and login
- PBKDF2-hashed passwords
- Session-based bearer auth
- AniList OAuth login flow
- SQLite-backed user/session storage

## Getting Started

### Prerequisites

- Python 3.10+
- `pip`
- Access to required upstream providers or local sidecar services for streaming/manga data

### Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configure environment

Create a `.env` file in the repository root and set any variables needed for your deployment. The app reads environment values at runtime, with defaults defined in `app/config.py`.

Common values include:

```env
ANILIST_CLIENT_ID=
ANILIST_CLIENT_SECRET=
FRONTEND_URL=http://127.0.0.1:5175
MANGA_VAULT_URL=http://127.0.0.1:8001
ANIVEXA_URL=http://127.0.0.1:8002
MOVIEBOX_URL=http://127.0.0.1:8003
ANIRAKU_URL=https://aniraku-backend-fhyy.onrender.com/api/v1
MIRURO_ENABLED=false
CF_CLEARANCE=
```

### Run the API

```bash
python run.py
```

This starts the app on `0.0.0.0:8000` using Uvicorn.

The application exposes Swagger/OpenAPI docs at:

```text
http://localhost:8000/docs
```

The root API information endpoint is:

```text
http://localhost:8000/api
```

## API Routes

Routes are included under the `/api` prefix. The application is organized with dedicated routers for:

- `health` – health status, ping endpoints, and image proxying
- `auth` – registration, login, logout, and AniList OAuth
- `anime` – anime search, collections, and detail access
- `streaming` – watch/stream resolution routes
- `manga` – manga metadata and chapter/image flows
- `movies` – movie-related endpoints

## Health and Monitoring

The service includes lightweight health checks and a simple activity ping endpoint:

- `/api/health`
- `/api/activity/ping`
- `/api/notifications/upcoming`

## Notes

- The app serves a static frontend only if a `/app/static` directory exists.
- CORS is enabled by configuration and defaults to permissive behavior unless overridden.
- Cache behavior and external provider settings are managed centrally in `app/config.py` and `app/services/catalog.py`.
- Local auth data is stored in `anidoom.db` by default.

## License

This repository does not currently declare a license in the GitHub metadata, so licensing terms should be confirmed before redistribution or reuse.

## Contributing

This project is intended to be run as a backend service for a frontend application. Contributions should preserve the existing provider abstraction pattern and keep environment-driven configuration centralized in `app/config.py`.
