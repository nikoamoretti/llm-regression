import unittest

from billing import Line, compute_invoice_total


class RegressionTests(unittest.TestCase):
    def test_undiscounted(self) -> None:
        total = compute_invoice_total(
            [Line(sku="PLAIN", quantity=1, unit_price_cents=100, discount_cents=0, tax_bps=0)]
        )
        self.assertEqual(total, 100)
