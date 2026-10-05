from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vendor.sdk import Client


def load_item(item_id: str) -> dict[str, str]:
    client = Client()
    # Old SDK: fetch(id) -> {"id", "name"}
    return client.fetch(item_id)
