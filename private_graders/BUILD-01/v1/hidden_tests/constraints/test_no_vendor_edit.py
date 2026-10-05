import os
import unittest
from pathlib import Path

WORKSPACE = Path(os.environ.get("WORKSPACE", "/workspace"))


class ConstraintTests(unittest.TestCase):
    def test_application_uses_new_api(self) -> None:
        source = (WORKSPACE / "src" / "client.py").read_text(encoding="utf-8")
        self.assertIn("get_item", source)
        self.assertNotIn(".fetch(", source)
