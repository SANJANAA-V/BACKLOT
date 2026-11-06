from __future__ import annotations

import os
from typing import Protocol


class Cache(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str, ttl: int | None = None) -> None: ...


class InMemoryCache:
    def __init__(self) -> None:
        self._d: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._d.get(key)

    def set(self, key: str, value: str, ttl: int | None = None) -> None:
        self._d[key] = value


class RedisCache:
    """Redis-backed cache. NOTE: not exercised against a real Redis in the authoring environment."""
    def __init__(self, url: str) -> None:
        import redis
        self._r = redis.Redis.from_url(url, decode_responses=True)

    def get(self, key: str) -> str | None:
        return self._r.get(key)

    def set(self, key: str, value: str, ttl: int | None = 86400) -> None:
        self._r.set(key, value, ex=ttl)


def cache_from_env() -> Cache:
    url = os.getenv("REDIS_URL")
    return RedisCache(url) if url else InMemoryCache()
