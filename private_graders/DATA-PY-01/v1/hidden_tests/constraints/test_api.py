import ast
import os
import unittest
from pathlib import Path

WORKSPACE = Path(os.environ.get("WORKSPACE", "/workspace"))


class ConstraintTests(unittest.TestCase):
    def test_migrate_exists(self) -> None:
        source = (WORKSPACE / "src" / "users.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
        self.assertIn("migrate", names)
        self.assertIn("load_users", names)
        self.assertIn("create_user", names)
