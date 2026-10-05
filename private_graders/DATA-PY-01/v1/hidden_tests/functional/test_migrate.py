import json
import tempfile
import unittest
from pathlib import Path

from users import create_user, load_users, migrate, save_users


class MigrationTests(unittest.TestCase):
    def test_default_display_name(self) -> None:
        upgraded = migrate(
            [{"id": "1", "email": "ada@example.com", "created_at": "t", "note": "keep"}]
        )
        self.assertEqual(upgraded[0]["display_name"], "ada")
        self.assertEqual(upgraded[0]["note"], "keep")

    def test_load_old_file(self) -> None:
        users = load_users(Path(__file__).resolve().parents[3] / "does-not-matter") if False else None
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "users.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "users": [{"id": "1", "email": "ada@example.com", "created_at": "t"}],
                    }
                ),
                encoding="utf-8",
            )
            users = load_users(path)
        self.assertEqual(users[0]["display_name"], "ada")

    def test_create_requires_display_name(self) -> None:
        users: list[dict] = []
        create_user(users, "lin@example.com", "Lin")
        self.assertEqual(users[0]["display_name"], "Lin")
        with self.assertRaises(TypeError):
            create_user(users, "nope@example.com")  # type: ignore[misc]

    def test_save_schema_v2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"
            save_users(path, [{"id": "1", "email": "a@b.c", "display_name": "A"}])
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 2)
