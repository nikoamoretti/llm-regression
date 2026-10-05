# Paid live smoke reports success for failed or evidence-less runs

The opt-in paid smoke runner (`apps/api/forecastlab_api/paid_smoke.py`) is supposed to
prove that one real model-plus-search forecast worked end to end. It currently passes
far too easily:

- `_audit` accepts a run whose status is `failed`.
- It accepts a ledger where every model or search request failed, as long as one was
  recorded, and it ignores ledger rows left in a non-terminal state such as `reserved`.
- It does not require a `ForecastVersion` or a probability for the run.
- It does not require any accepted, external (non-fixture) evidence.
- `main()` lets exceptions escape as a traceback instead of exiting cleanly with a
  non-zero code.
- `execute_live_smoke` opens a database session without applying migrations first, so it
  fails on a fresh database.

Expected behaviour:

- `_audit(session, run, *, expected_model, expected_search, profile)` keeps its signature
  and raises `RuntimeError` with these exact messages. It does not require `ForecastRunAttempt`
  rows and accepts ledger rows whose token and cost fields are NULL:
  - run status `failed` → `smoke_forecast_failed`. Any other status that is not
    `completed` still raises `run_not_terminal:<status>`.
  - any ledger row whose status is not `succeeded`, `failed` or `released` →
    `smoke_nonterminal_ledger_entry`.
  - no model ledger row with status `succeeded` → `smoke_no_successful_model_request`.
  - no search ledger row with status `succeeded` → `smoke_no_successful_search_request`.
    A failed attempt followed by a successful retry is fine, and the failed row still
    appears in the returned `payload["ledger"]`.
  - no accepted external evidence → `smoke_no_accepted_external_evidence`. An evidence
    item counts only if it is not `rejected`, is `as_of_eligible`, and has an `http`/`https`
    URL whose host is not internal or a fixture host (`fixtures.forecastlab.local`, any
    `*.forecastlab.local`, `localhost`, `127.0.0.1`, `::1`). Evidence from the fixture
    host must still raise the existing `mock_or_fixture_evidence_used`. An item is accepted
    exactly when those three conditions hold; no other `EvidenceItem` field (content hash, title,
    excerpt, publication date, snapshot status) is checked.
  - no `ForecastVersion` for the run → `smoke_forecast_version_missing`.
  - version with `ensemble_probability` of `None` → `smoke_probability_missing`.
  - probability outside `[0.01, 0.99]` → `smoke_probability_out_of_range`.
  - A completed run that passes every check still returns the payload as before, with
    `status` and `probability` (for example `0.22`).
- `execute_live_smoke` must call `apply_schema()` (from `forecastlab_api.migrate`) before
  it opens any database session. `paid_smoke` must import it under the module-level name
  `apply_schema` so that `forecastlab_api.paid_smoke.apply_schema` can be patched. If
  `execute_run` raises and the refreshed run has status `failed`, raise
  `RuntimeError("smoke_forecast_failed")`.
- `main()`:
  - Without opt-in it prints the absent message, returns 0 and never calls
    `execute_live_smoke`. With missing credentials it returns 2 and never calls it.
    Both are unchanged.
  - If `execute_live_smoke` raises, print `paid_smoke_failed:<reason>` to **stderr** and
    return 1. `<reason>` is the exception message with secrets redacted
    (`forecastlab.hashing.redact_secrets`), collapsed to one line, at most 180 characters.
    A message such as `smoke_forecast_failed` must come through unchanged, giving
    `paid_smoke_failed:smoke_forecast_failed`.
  - On success, print the result as today (including `status=completed`) and return 0,
    with nothing written to stderr that starts with `paid_smoke_failed:`.

Keep existing behaviour and tests passing.
