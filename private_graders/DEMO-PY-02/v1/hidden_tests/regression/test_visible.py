import unittest

from slug import slugify


class RegressionTests(unittest.TestCase):
    def test_single_word(self) -> None:
        self.assertEqual(slugify("Hello"), "hello")

    def test_digits_are_kept(self) -> None:
        self.assertEqual(slugify("Top 10"), "top-10")
