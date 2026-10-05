import unittest

from pagination import page_count, page_items


class PaginationTests(unittest.TestCase):
    def test_partial_last_page_counts(self) -> None:
        self.assertEqual(page_count(21, 10), 3)
        self.assertEqual(page_count(1, 10), 1)

    def test_zero_items_is_zero_pages(self) -> None:
        self.assertEqual(page_count(0, 10), 0)

    def test_first_page_is_page_one(self) -> None:
        self.assertEqual(page_items(list(range(25)), 1, 10), list(range(10)))

    def test_last_partial_page(self) -> None:
        self.assertEqual(page_items(list(range(25)), 3, 10), [20, 21, 22, 23, 24])

    def test_page_past_the_end_is_empty(self) -> None:
        self.assertEqual(page_items(list(range(5)), 2, 10), [])

    def test_rejects_page_zero(self) -> None:
        with self.assertRaises(ValueError):
            page_items([1, 2, 3], 0, 2)
