"""Tiny async TTL cache. Good enough for a self-hosted API; swap for Redis later."""
from __future__ import annotations

import asyncio
import time
from typing import Any


class TTLCache:
    def __init__(self, ttl: float = 600.0) -> None:
        self._ttl = ttl
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    async def get_or_set(self, key: str, factory) -> Any:
        """Return cached value or compute it via the async `factory` and cache it."""
        now = time.monotonic()
        hit = self._store.get(key)
        if hit and now - hit[0] < self._ttl:
            return hit[1]

        async with self._lock:
            # Double-checked under lock
            hit = self._store.get(key)
            if hit and time.monotonic() - hit[0] < self._ttl:
                return hit[1]
            value = await factory()
            self._store[key] = (time.monotonic(), value)
        return value

    async def clear(self, prefix: str = "") -> int:
        async with self._lock:
            keys = [k for k in self._store if k.startswith(prefix)]
            for k in keys:
                del self._store[k]
            return len(keys)


cache = TTLCache()
# Shorter TTL for fast-rotating data (episode sources / m3u8 links)
short_cache = TTLCache(ttl=60.0)
