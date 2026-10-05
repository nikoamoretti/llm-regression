"""A tiny in-process TTL cache with an injectable clock."""

from __future__ import annotations

from typing import Callable, Hashable, TypeVar

T = TypeVar("T")
Clock = Callable[[], float]


class TTLCache:
    def __init__(self, ttl_seconds: float, clock: Clock | None = None) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.ttl_seconds = float(ttl_seconds)
        self._clock = clock or __import__("time").monotonic
        self._store: dict[Hashable, tuple[object, float]] = {}

    def set(self, key: Hashable, value: object) -> None:
        self._store[key] = (value, self._clock() + self.ttl_seconds)

    def get(self, key: Hashable, default: T | None = None) -> object | T | None:
        item = self._store.get(key)
        if item is None:
            return default
        value, expires_at = item
        now = self._clock()
        if now >= expires_at:
            del self._store[key]
            return default
        return value

    def contains(self, key: Hashable) -> bool:
        missing = object()
        return self.get(key, missing) is not missing
