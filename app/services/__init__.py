"""Services layer — re-exports the catalog facade used by routers."""
from .catalog import (
    clear_cache,
    details,
    mal_raw,
    popular,
    recent,
    schedule,
    search,
    trending,
    upcoming,
)

__all__ = [
    "search",
    "details",
    "trending",
    "popular",
    "upcoming",
    "recent",
    "schedule",
    "mal_raw",
    "clear_cache",
]
