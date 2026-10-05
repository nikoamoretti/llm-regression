import ast
import unittest
from pathlib import Path
import os

WORKSPACE = Path(os.environ.get("WORKSPACE", "/workspace"))


class ConstraintTests(unittest.TestCase):
    def test_no_third_party_imports(self) -> None:
        source = (WORKSPACE / "src" / "ttl_cache.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        allowed = {"time", "typing", "annotations", "__future__"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertIn(alias.name.split(".")[0], allowed)
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertIn(node.module.split(".")[0], allowed)
