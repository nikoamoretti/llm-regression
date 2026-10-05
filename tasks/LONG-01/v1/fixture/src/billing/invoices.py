from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Line:
    sku: str
    quantity: int
    unit_price_cents: int
    discount_cents: int
    tax_bps: int


def compute_invoice_total(lines: list[Line]) -> int:
    total = 0
    for line in lines:
        base = line.quantity * line.unit_price_cents
        # BUG: tax is applied before the discount.
        taxed = base + base * line.tax_bps // 10_000
        total += taxed - line.discount_cents
    return total
