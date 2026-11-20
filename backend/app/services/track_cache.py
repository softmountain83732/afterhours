"""In-memory TTL cache with request coalescing for track metadata queries."""

import asyncio
import time
from typing import Optional, Dict, Any, Callable, Awaitable
import logging

logger = logging.getLogger("track_cache")


class TrackResponseCache:
    """
    Process-local in-memory TTL response cache for GET /api/tracks.
    Features:
      - 30-second TTL.
      - In-flight request coalescing (singleflight) to prevent thundering herd under multi-device load.
      - Bounded memory (LRU eviction past max_entries).
      - Explicit cache invalidation on track ingestion, update, or deletion.
    """
    def __init__(self, ttl_seconds: float = 30.0, max_entries: int = 500):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._in_flight: Dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()

    def _evict_expired_or_overflow(self, now: float):
        # Remove expired
        expired_keys = [k for k, v in self._cache.items() if now >= v["expires_at"]]
        for k in expired_keys:
            self._cache.pop(k, None)

        # Evict oldest if exceeding capacity
        if len(self._cache) > self.max_entries:
            sorted_entries = sorted(self._cache.items(), key=lambda item: item[1]["last_accessed"])
            excess = len(self._cache) - self.max_entries
            for k, _ in sorted_entries[:excess]:
                self._cache.pop(k, None)

    async def get_or_compute(
        self,
        key: str,
        compute_fn: Callable[[], Awaitable[Dict[str, Any]]]
    ) -> Dict[str, Any]:
        now = time.monotonic()

        # Check existing fresh cache
        async with self._lock:
            self._evict_expired_or_overflow(now)
            entry = self._cache.get(key)
            if entry and now < entry["expires_at"]:
                entry["last_accessed"] = now
                return entry["data"]

            # Check if identical request is already in-flight
            future = self._in_flight.get(key)
            is_leader = False
            if future is None:
                loop = asyncio.get_running_loop()
                future = loop.create_future()
                self._in_flight[key] = future
                is_leader = True

        if not is_leader:
            # Wait for in-flight request to resolve
            return await future

        # Leader executes computation
        try:
            data = await compute_fn()
            now = time.monotonic()
            async with self._lock:
                self._cache[key] = {
                    "data": data,
                    "expires_at": now + self.ttl_seconds,
                    "last_accessed": now,
                }
                self._in_flight.pop(key, None)
                if not future.done():
                    future.set_result(data)
            return data
        except Exception as exc:
            async with self._lock:
                self._in_flight.pop(key, None)
                if not future.done():
                    future.set_exception(exc)
            raise

    def invalidate(self):
        """Invalidate all cached track metadata entries."""
        self._cache.clear()
        logger.info("Track response cache invalidated.")


track_response_cache = TrackResponseCache(ttl_seconds=30.0, max_entries=500)
