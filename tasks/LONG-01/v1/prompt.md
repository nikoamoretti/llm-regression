This repository contains many similar-looking packages. Invoice totals are wrong.

Find `compute_invoice_total` in the billing package and make it:

1. start from line `quantity * unit_price_cents`
2. subtract `discount_cents`
3. then apply `tax_bps` to the discounted subtotal

The current implementation taxes the pre-discount amount. Do not change unrelated packages. Hidden end-to-end tests use SKU `ORB-9`.
