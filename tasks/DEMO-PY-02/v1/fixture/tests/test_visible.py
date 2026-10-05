import unittest

from slug import slugify


class VisibleSlugTests(unittest.TestCase):
    def test_single_word(self) -> None:
        self.assertEqual(slugify("Hello"), "hello")

    def test_two_words(self) -> None:
        self.assertEqual(slugify("hello world"), "hello-world")


if __name__ == "__main__":
    unittest.main()
