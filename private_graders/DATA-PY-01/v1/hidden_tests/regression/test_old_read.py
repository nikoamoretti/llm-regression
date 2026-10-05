import json
import os
import tempfile
import unittest
from pathlib import Path

from users import load_users

WORKSPACE = Path(os.environ.get("WORKSPACE", "/workspace"))


class RegressionTests(unittest.TestCase):
    def test_fixture_file_still_loads(self) -> None:
        users = load_users(WORKSPACE / "data" / "users.json")
        self.assertEqual(users[0]["email"], "ada@example.com")
