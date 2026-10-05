import os
import unittest
from pathlib import Path

WORKSPACE = Path(os.environ.get("WORKSPACE", "/workspace"))


class ConstraintTests(unittest.TestCase):
    def test_split_files_are_nonempty(self) -> None:
        for name in ("ast_nodes.py", "lexer.py", "parser.py"):
            path = WORKSPACE / "src" / name
            self.assertGreater(path.stat().st_size, 20)
