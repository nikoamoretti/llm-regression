import unittest

from parse_expr import parse


class RegressionTests(unittest.TestCase):
    def test_simple_number(self) -> None:
        self.assertEqual(parse("42").value, 42)

    def test_rejects_junk(self) -> None:
        with self.assertRaises(ValueError):
            parse("1 + $")
