import unittest

from ttl_cache import TTLCache


class Clock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class RegressionTests(unittest.TestCase):
    def test_fresh_read(self) -> None:
        cache = TTLCache(5, clock=Clock(1))
        cache.set("a", 1)
        self.assertEqual(cache.get("a"), 1)

    def test_long_after_ttl(self) -> None:
        clock = Clock(0)
        cache = TTLCache(5, clock=clock)
        cache.set("a", 1)
        clock.now = 11
        self.assertIsNone(cache.get("a"))
