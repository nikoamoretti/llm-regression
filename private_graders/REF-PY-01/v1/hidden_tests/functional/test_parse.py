import os
import unittest
from pathlib import Path

from parse_expr import parse
from ast_nodes import Binary, Number, Unary

WORKSPACE = Path(os.environ.get("WORKSPACE", "/workspace"))


class ParseTests(unittest.TestCase):
    def test_precedence(self) -> None:
        tree = parse("1+2*3")
        self.assertEqual(tree, Binary("+", Number(1), Binary("*", Number(2), Number(3))))

    def test_unary_and_parens(self) -> None:
        tree = parse("-(2+3)*4")
        self.assertEqual(
            tree,
            Binary("*", Unary("-", Binary("+", Number(2), Number(3))), Number(4)),
        )

    def test_modules_exist(self) -> None:
        src = WORKSPACE / "src"
        self.assertTrue((src / "ast_nodes.py").is_file())
        self.assertTrue((src / "lexer.py").is_file())
        self.assertTrue((src / "parser.py").is_file())
        lexer = (src / "lexer.py").read_text(encoding="utf-8")
        self.assertIn("def tokenize", lexer)
        self.assertIn("Token", lexer)
