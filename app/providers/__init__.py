"""Metadata providers.

Each provider implements the async ``MetadataProvider`` protocol below and is
registered in ``app.services.catalog``. Adding a new source (e.g. Malkan) is:
  1. create providers/<name>.py implementing the protocol
  2. register it in services/catalog.py
No router or frontend changes required.
"""
from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str | None) -> str | None:
    """Strip HTML tags and collapse whitespace (AniList descriptions are HTML)."""
    if not text:
        return None
    return re.sub(r"\s+", " ", _TAG_RE.sub("", text)).strip()


def iso_date(date: dict | None) -> str | None:
    """Convert AniList startDate/endDate {year,month,day} to ISO string.

    AniList may return ``month: null``/``day: null`` when only the year is
    known, so fall back with ``or 1`` (not ``.get(key, default)``, which only
    defaults on *missing* keys).
    """
    if not date or not date.get("year"):
        return None
    month = date.get("month") or 1
    day = date.get("day") or 1
    return f"{date['year']:04d}-{month:02d}-{day:02d}"


@runtime_checkable
class MetadataProvider(Protocol):
    """Interface every metadata provider must satisfy."""

    name: str

    async def search(self, query: str, page: int = 1, per_page: int = 20) -> dict:
        """Return a page dict: {page, perPage, total, hasNextPage, results:[dict]}"""
        ...

    async def details(self, media_id: int) -> dict | None:
        """Return the full normalized media dict or None."""
        ...

    async def trending(self, page: int = 1, per_page: int = 20) -> dict:
        ...
