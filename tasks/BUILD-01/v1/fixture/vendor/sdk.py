from __future__ import annotations

from typing import Any


class Client:
    def __init__(self, catalog: dict[str, dict[str, Any]] | None = None) -> None:
        self.catalog = catalog or {
            "42": {"id": "42", "name": "widget", "sku": "W-42"},
        }

    def get_item(self, item_id: str, fields: tuple[str, ...] = ("id", "name")) -> dict[str, Any]:
        row = self.catalog[item_id]
        return {key: row[key] for key in fields}
