# Paid smoke audit output is incomplete and its probability check is too loose

The paid live smoke auditor (`apps/api/forecastlab_api/paid_smoke.py`) has several gaps:

- Ledger rows with status `released` are treated as fine, even though a released
  reservation means the call never completed.
- The probability check only catches out-of-range numbers. Non-numeric values crash with
  a raw exception, and NaN slips through.
- The audit payload and printed output do not say which external evidence was accepted.
  The printed ledger uses Python `dict` repr, which machines cannot parse, and several
  payload fields are never printed.

Expected behaviour:

- Ledger status rules in `_audit`:
  - Any row with status `released` → `RuntimeError("smoke_released_ledger_entry")`. This
    check comes before the generic non-terminal check.
  - Any other row whose status is not `succeeded` or `failed` (for example `reserved`,
    `running`, `pending`, `unknown`) → `RuntimeError("smoke_nonterminal_ledger_entry")`.
- Add a module-level function `_require_valid_probability(value) -> float` in
  `paid_smoke.py`:
  - `None` → `RuntimeError("smoke_probability_missing")`.
  - A value that cannot be converted to float (for example `"not-a-number"`), NaN, ±inf,
    or anything outside `[0.01, 0.99]` (so `0.0`, `1.0`, `-0.1` and `1.1` all fail) →
    `RuntimeError("smoke_probability_invalid")`.
  - Otherwise return the value as a `float`.
  - `_audit` must use it for the forecast version's `ensemble_probability`, so an invalid
    stored probability raises `smoke_probability_invalid`. The old
    `smoke_probability_out_of_range` code goes away. `payload["probability"]` is the
    validated float.
- The audit payload gains:
  - `accepted_external_evidence_count`: the number of accepted evidence items, counting
    duplicates.
  - `accepted_evidence_urls`: the sorted, de-duplicated list of their URLs.

  For example, two accepted items with the same BLS URL, one rejected item and one
  ineligible item give a count of `2` and a single URL. Rejected or not-`as_of_eligible`
  items are never counted.
- Return the payload's `ledger` rows in a stable order, sorted by stage, provider type,
  physical attempt number, then id.
- `print_result(payload)` prints one `key=value` line each for `run_id`, `status`,
  `probability`, `model_provider`, `search_provider`, `model_cost_usd`, `search_cost_usd`,
  `failed_attempt_cost_usd`, `total_cost_usd`, `cost_source`, `run_attempt_count`,
  `provider_request_count` and `accepted_external_evidence_count`. It then prints one
  `accepted_evidence_url=<url>` line per accepted URL, then `checked_at=<value>`, then a
  line `ledger:`. After `ledger:` comes only one JSON object per line (sorted keys) for
  each ledger row. Nothing else follows.
- Failure output from `main()` (`paid_smoke_failed:<reason>` on stderr, exit code 1) must
  never contain raw API keys such as `xai-…`, `sk-…` or `tvly-…`. They must appear
  redacted, so the output contains `REDACTED`.

Keep existing behaviour and tests passing.
