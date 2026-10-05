import unittest

from pagination import page_count, page_items


class VisiblePaginationTests(unittest.TestCase):
    def test_full_pages(self) -> None:
        self.assertEqual(page_count(20, 10), 2)

    def test_rejects_zero_per_page(self) -> None:
        with self.assertRaises(ValueError):
            page_count(5, 0)


if __name__ == "__main__":
    unittest.main()
