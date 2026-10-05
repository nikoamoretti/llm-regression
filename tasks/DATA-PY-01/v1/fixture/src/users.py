from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_users(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return list(payload["users"])


def create_user(users: list[dict[str, Any]], email: str) -> dict[str, Any]:
    user = {
        "id": str(len(users) + 1),
        "email": email,
        "created_at": "2026-09-11T00:00:00Z",
    }
    users.append(user)
    return user


def save_users(path: Path, users: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps({"users": users}, indent=2), encoding="utf-8")
