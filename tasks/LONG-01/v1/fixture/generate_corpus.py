#!/usr/bin/env python3
"""Deterministic padding modules so LONG-01 can scale context without git-bloating."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FAST = os.environ.get("SOL_REGRESSION_FAST", "1") == "1"
COUNT = 20 if FAST else 120
SEED_NAMES = [
    "auth",
    "billing_notes",
    "catalog",
    "search",
    "shipping",
    "notifications",
    "analytics",
    "ledger",
    "inventory_view",
    "promo",
]


def module_text(index: int) -> str:
    family = SEED_NAMES[index % len(SEED_NAMES)]
    return f'''"""Synthetic package {family}_{index} for long-context navigation."""

from __future__ import annotations

DEFAULT_RATE_{index} = {100 + index}

def describe_{family}_{index}(sku: str) -> dict[str, str | int]:
    """Look-alike helper. Not the invoice total implementation."""
    return {{
        "sku": sku,
        "family": "{family}",
        "index": {index},
        "note": "ignore this module when computing invoice totals",
        "rate": DEFAULT_RATE_{index},
    }}


def decoy_total_{index}(amount: int) -> int:
    return amount + DEFAULT_RATE_{index}
'''


def main() -> None:
    target = ROOT / "packages" / "padding"
    target.mkdir(parents=True, exist_ok=True)
    (target / "__init__.py").write_text("", encoding="utf-8")
    for index in range(COUNT):
        (target / f"mod_{index:04d}.py").write_text(module_text(index), encoding="utf-8")


if __name__ == "__main__":
    main()
