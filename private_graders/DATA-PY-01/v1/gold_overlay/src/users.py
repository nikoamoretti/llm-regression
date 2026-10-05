from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _default_display_name(email: str) -> str:
    return email.split("@", 1)[0]


def migrate(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    upgraded = []
    for record in records:
        item = dict(record)
        item.setdefault("display_name", _default_display_name(str(item.get("email", ""))))
        upgraded.append(item)
    return upgraded


def load_users(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return migrate(list(payload.get("users", [])))


def create_user(users: list[dict[str, Any]], email: str, display_name: str) -> dict[str, Any]:
    if not display_name:
        raise ValueError("display_name is required")
    user = {
        "id": str(len(users) + 1),
        "email": email,
        "created_at": "2026-09-11T00:00:00Z",
        "display_name": display_name,
    }
    users.append(user)
    return user


def save_users(path: Path, users: list[dict[str, Any]]) -> None:
    path.write_text(
        json.dumps({"schema_version": 2, "users": users}, indent=2),
        encoding="utf-8",
    )
