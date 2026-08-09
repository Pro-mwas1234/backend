"""Malkan provider (⚠️ NOT FOUND — pluggable stub).

Research found no public API, domain, or GitHub project named "Malkan"
(GitHub API search, web search and domain lookups all returned nothing).
Per project decision it's skipped for now — this module is the drop-in slot.

To add a real Malkan API later:
  1. Implement search()/details()/trending() against the real endpoint.
  2. Keep the returned dict shape compatible with anilist.py's _normalize()
     (id, title{romaji,english,native}, coverImage, synopsis, ...).
  3. Register it in app/services/catalog.py's provider chain.

See docs/DATA_SOURCES.md for details.
"""
from __future__ import annotations


class MalkanProvider:
    name = "malkan"

    async def search(self, query: str, page: int = 1, per_page: int = 20) -> dict:
        raise NotImplementedError(
            "Malkan is not available: no public API found. "
            "Add the real implementation in app/providers/malkan.py."
        )

    async def details(self, media_id: int) -> dict | None:
        raise NotImplementedError(
            "Malkan is not available: no public API found. "
            "Add the real implementation in app/providers/malkan.py."
        )

    async def trending(self, page: int = 1, per_page: int = 20) -> dict:
        raise NotImplementedError(
            "Malkan is not available: no public API found. "
            "Add the real implementation in app/providers/malkan.py."
        )


malkan = MalkanProvider()
