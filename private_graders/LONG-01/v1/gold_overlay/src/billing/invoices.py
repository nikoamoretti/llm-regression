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
        discounted = base - line.discount_cents
        taxed = discounted + discounted * line.tax_bps // 10_000
        total += taxed
    return total
