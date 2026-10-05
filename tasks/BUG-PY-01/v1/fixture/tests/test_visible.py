import unittest

from ttl_cache import TTLCache


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class VisibleCacheTests(unittest.TestCase):
    def test_set_then_get(self) -> None:
        clock = FakeClock(10)
        cache = TTLCache(5, clock=clock)
        cache.set("a", 1)
        self.assertEqual(cache.get("a"), 1)

    def test_expired_after_twice_ttl(self) -> None:
        clock = FakeClock(0)
        cache = TTLCache(5, clock=clock)
        cache.set("a", 1)
        clock.now = 11
        self.assertIsNone(cache.get("a"))


if __name__ == "__main__":
    unittest.main()
