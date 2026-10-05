import unittest

from ttl_cache import TTLCache


class Clock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class BoundaryTests(unittest.TestCase):
    def test_exact_boundary_is_expired(self) -> None:
        clock = Clock(0)
        cache = TTLCache(10, clock=clock)
        cache.set("k", "v")
        clock.now = 10
        self.assertIsNone(cache.get("k"))
        self.assertFalse(cache.contains("k"))

    def test_just_before_boundary_is_valid(self) -> None:
        clock = Clock(0)
        cache = TTLCache(10, clock=clock)
        cache.set("k", "v")
        clock.now = 9.999
        self.assertEqual(cache.get("k"), "v")
        self.assertTrue(cache.contains("k"))

    def test_reset_on_overwrite(self) -> None:
        clock = Clock(0)
        cache = TTLCache(5, clock=clock)
        cache.set("k", 1)
        clock.now = 4
        cache.set("k", 2)
        clock.now = 8
        self.assertEqual(cache.get("k"), 2)
        clock.now = 9
        self.assertIsNone(cache.get("k"))

    def test_none_value_is_still_present(self) -> None:
        clock = Clock(0)
        cache = TTLCache(3, clock=clock)
        cache.set("n", None)
        self.assertTrue(cache.contains("n"))
        self.assertIsNone(cache.get("n", default="missing"))
        clock.now = 3
        self.assertFalse(cache.contains("n"))
