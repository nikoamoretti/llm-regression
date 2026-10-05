import unittest

from client import load_item


class AdapterTests(unittest.TestCase):
    def test_load_known_item(self) -> None:
        item = load_item("42")
        self.assertEqual(item["id"], "42")
        self.assertEqual(item["name"], "widget")
        self.assertNotIn("sku", item)
