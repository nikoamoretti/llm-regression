import os
import unittest
from pathlib import Path

WORKSPACE = Path(os.environ.get("WORKSPACE", "/workspace"))


class RegressionTests(unittest.TestCase):
    def test_vendor_still_has_get_item(self) -> None:
        source = (WORKSPACE / "vendor" / "sdk.py").read_text(encoding="utf-8")
        self.assertIn("def get_item", source)
        self.assertNotIn("def fetch", source)
