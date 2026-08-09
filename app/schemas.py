"""Pydantic models for the unified API surface."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class MediaTitle(BaseModel):
    romaji: Optional[str] = None
    english: Optional[str] = None
    native: Optional[str] = None

    @property
    def preferred(self) -> str:
        return self.english or self.romaji or self.native or "Untitled"


class DateField(BaseModel):
    year: Optional[int] = None
    month: Optional[int] = None
    day: Optional[int] = None


class NextAiring(BaseModel):
    episode: int
    airingAt: int
    timeUntilAiring: int


class Media(BaseModel):
    id: int
    idMal: Optional[int] = None
    title: MediaTitle
    synopsis: Optional[str] = None
    coverImage: Optional[str] = None
    bannerImage: Optional[str] = None
    format: Optional[str] = None
    season: Optional[str] = None
    seasonYear: Optional[int] = None
    episodes: Optional[int] = None
    duration: Optional[int] = None
    status: Optional[str] = None
    score: Optional[int] = None
    meanScore: Optional[int] = None
    popularity: Optional[int] = None
    genres: list[str] = Field(default_factory=list)
    studios: list[dict[str, Any]] = Field(default_factory=list)
    nextAiringEpisode: Optional[NextAiring] = None
    startDate: Optional[str] = None
    endDate: Optional[str] = None
    isAdult: bool = False
    trailer: Optional[dict[str, Any]] = None
    characters: list[dict[str, Any]] = Field(default_factory=list)
    relations: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[dict[str, Any]] = Field(default_factory=list)
    # Jikan enrichment is merged here when available
    mal: Optional[dict[str, Any]] = None

    class Config:
        arbitrary_types_allowed = True


class Page(BaseModel):
    page: int
    perPage: int
    total: int
    hasNextPage: bool
    results: list[Media]


class Episode(BaseModel):
    id: str
    # AniList episode ids can be fractional (interludes/specials, e.g. 1088.5).
    number: int | float
    title: Optional[str] = None
    image: Optional[str] = None
    airDate: Optional[str] = None
    duration: Optional[int] = None
    description: Optional[str] = None
    filler: bool = False


class ProviderEpisodes(BaseModel):
    name: str
    sub: list[Episode] = Field(default_factory=list)
    dub: list[Episode] = Field(default_factory=list)


class EpisodesResponse(BaseModel):
    anilistId: int
    mappings: dict[str, Any] = Field(default_factory=dict)
    providers: list[ProviderEpisodes]


class StreamSource(BaseModel):
    url: str
    type: str = "hls"
    quality: Optional[str] = None
    server: Optional[str] = None
    # Referer the upstream CDN expects — the HLS worker sends it when set.
    referer: Optional[str] = None


class SubtitleTrack(BaseModel):
    file: str
    label: Optional[str] = None
    kind: str = "captions"


class Interval(BaseModel):
    start: float = 0
    end: float = 0


class SourcesResponse(BaseModel):
    episodeId: str
    provider: Optional[str] = None
    streams: list[StreamSource]
    subtitles: list[SubtitleTrack] = Field(default_factory=list)
    intro: Interval = Field(default_factory=Interval)
    outro: Interval = Field(default_factory=Interval)
    # Extra fetch headers (e.g. Referer) returned by some upstreams.
    headers: dict[str, Any] = Field(default_factory=dict)


class Health(BaseModel):
    status: str
    # Streaming provider config (not liveness): Miruro is disabled by default
    # (upstream 403s without a fresh cf_clearance); Anivexa is the primary.
    miruro_enabled: bool
    miruro_clearance: bool
    anivexa_url: str
    aniraku_enabled: bool
    moviebox_url: str
