import unittest

from slug import slugify


class SlugTests(unittest.TestCase):
    def test_runs_collapse_to_one_hyphen(self) -> None:
        self.assertEqual(slugify("a  --  b"), "a-b")

    def test_ends_are_trimmed(self) -> None:
        self.assertEqual(slugify("  Hello, World!  "), "hello-world")

    def test_non_ascii_is_a_separator(self) -> None:
        self.assertEqual(slugify("café au lait"), "caf-au-lait")

    def test_no_letters_is_empty(self) -> None:
        self.assertEqual(slugify("!!! ---"), "")
