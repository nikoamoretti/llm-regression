import unittest

from billing import Line, compute_invoice_total


class InvoiceTests(unittest.TestCase):
    def test_orb9_discount_then_tax(self) -> None:
        total = compute_invoice_total(
            [Line(sku="ORB-9", quantity=2, unit_price_cents=1000, discount_cents=200, tax_bps=1000)]
        )
        # (2000 - 200) * 1.10 = 1980
        self.assertEqual(total, 1980)

    def test_zero_tax(self) -> None:
        total = compute_invoice_total(
            [Line(sku="ORB-9", quantity=1, unit_price_cents=500, discount_cents=50, tax_bps=0)]
        )
        self.assertEqual(total, 450)
