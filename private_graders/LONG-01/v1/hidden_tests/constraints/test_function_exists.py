import os
import unittest
from pathlib import Path

WORKSPACE = Path(os.environ.get("WORKSPACE", "/workspace"))


class ConstraintTests(unittest.TestCase):
    def test_billing_module_still_present(self) -> None:
        self.assertTrue((WORKSPACE / "src" / "billing" / "invoices.py").is_file())
        source = (WORKSPACE / "src" / "billing" / "invoices.py").read_text(encoding="utf-8")
        self.assertIn("def compute_invoice_total", source)
