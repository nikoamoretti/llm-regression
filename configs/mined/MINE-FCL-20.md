# Make the paid live smoke an executable, fail-closed, auditable check (and report experiment spend)

`scripts/paid_smoke.py` today only prints instructions; it never runs a forecast and cannot tell us whether a real model + real search round-trip actually works. We need a real, opt-in live smoke that runs one forecast end to end through the normal pipeline, audits what happened against the database, and fails closed on anything suspicious. Separately, the benchmark experiment summary has no spend breakdown.

## 1. New module `forecastlab_api.paid_smoke` (in `apps/api/forecastlab_api/paid_smoke.py`)

`scripts/paid_smoke.py` should become a thin wrapper that puts `packages/forecasting` and `apps/api` on `sys.path` and calls `forecastlab_api.paid_smoke.main()`.

The module must expose these public names (tests import all of them): `ABSENT_MESSAGE`, `CREDENTIALS_MISSING_MESSAGE`, `SMOKE_PROFILE_ID`, `_audit`, `_require_valid_probability`, `credentials_ready`, `execute_live_smoke`, `main`, `opted_in`, `print_result`.

Tests monkeypatch these names **on the `forecastlab_api.paid_smoke` module**, so the module must look them up there at call time: `working_tree_dirty`, `load_secrets`, `apply_schema` (from `forecastlab_api.migrate`), `credentials_ready`, `execute_live_smoke`, `_session_factory`. Call each of them with no arguments: `working_tree_dirty()`, `load_secrets()`, `apply_schema()`, `_session_factory()`, and inside `main` `credentials_ready()` and `execute_live_smoke()`.

### Profile
`SMOKE_PROFILE_ID = "live_smoke_v1"`. Add `configs/forecast_profiles/live_smoke_v1.yaml`: a tightly capped, one-track (`current_evidence`) live smoke profile that the existing pipeline can execute: 1 subquestion per track, 1 search result and 1 fetch per subquestion, at most 3 model calls, 1 search call, 2 fetched documents, small token limits, and `max_estimated_cost_usd: 0.25`. `forecastlab.profiles.load_profile("live_smoke_v1")` must load it.

### Opt-in and credentials
- `opted_in()` is true only when the env var `FORECASTLAB_RUN_PAID_SMOKE` equals `"1"`.
- `credentials_ready(secrets=None)` (defaults to `load_secrets()`): true only when there is a non-empty `model_api_key` with a `model_provider` that is not empty/`mock`/`demo`, **and** a non-empty `search_api_key` with a `search_provider` that is not empty/`mock`/`demo`. `credentials_ready({"model_provider": "mock"})` is `False`.

### `main() -> int`
- Not opted in: print `ABSENT_MESSAGE` to stdout and return `0`. Nothing else may run (no provider calls, no `execute_live_smoke`).
- Opted in but `credentials_ready()` (called with no arguments) is false: print `CREDENTIALS_MISSING_MESSAGE` (value `"credentials_missing"`) to stdout and return `2`.
- Otherwise call `execute_live_smoke()` (no positional args). On success `print_result(payload)` and return `0`. On any exception, write `paid_smoke_failed:<reason>` to **stderr** and return `1`. `<reason>` is the exception message passed through `forecastlab.hashing.redact_secrets` and made single-token (no whitespace; only alphanumerics and `:_-.` kept), so `RuntimeError("smoke_forecast_failed")` yields exactly `paid_smoke_failed:smoke_forecast_failed`, and a message containing `xai-…`, `sk-…` or `tvly-…` keys must not leak the key (output contains `REDACTED`).

### `execute_live_smoke(*, session_factory=None) -> dict`
Refuse (raise `RuntimeError`) if the working tree is dirty (`working_tree_dirty`) or `uv.lock` is missing at the repo root, or credentials are not ready. Expected model/search providers come from the secrets. Call `apply_schema()` **before** opening any database session (the order of calls must be exactly: `apply_schema`, then the session factory). When `session_factory` is omitted, use `_session_factory()`, which returns `forecastlab_api.db.SessionLocal` resolved at call time. Inside the session: create a draft live `Question` (a fixed US U-3 unemployment ≥ 5.0% by mid-2027 question) with a manually specified `ResolutionContract` saved via `save_contract`, start a `live` run on `live_smoke_v1` with `start_run(..., enqueue=False)`, commit, and execute it with `execute_run`. If execution raises and the run ended `failed`, raise `RuntimeError("smoke_forecast_failed")`. Then return `_audit(...)` and commit.

With stubbed HTTP clients for an `xai` model and `tavily` search (both non-mock), the returned payload must have status `completed`, a probability within [0.01, 0.99], `model_provider == "xai"`, `search_provider == "tavily"`, a non-empty `ledger` containing both providers and never `openai_compatible`, and `total_cost_usd <= 0.25`; the persisted evidence must not be fixture evidence and the ledger must contain succeeded model and search rows.

### `_audit(session, run, *, expected_model, expected_search, profile) -> dict`
Reads the run's `ForecastRunAttempt`, `ProviderCallLedger`, `EvidenceItem` rows, its `execution_context_json` and its `ForecastVersion`, and raises `RuntimeError` with these codes (each test violates exactly one condition, so make sure the checks are ordered so the stated code wins). The payload's `probability` is the value returned by `_require_valid_probability(version.ensemble_probability)`. `_audit` must not require `ForecastRunAttempt` rows (tests seed none, so the attempt count may be 0) and must tolerate ledger rows whose token and cost fields are NULL:
- `mock_or_fixture_evidence_used` — execution context says model/search is mock or fixture evidence was used, `run.fixture_evidence_used`, or any evidence URL is on host `fixtures.forecastlab.local` (this must win over the "no accepted evidence" check).
- provider identity mismatch against `expected_model` / `expected_search` (context or ledger) — `vendor_identity_incorrect…` / `search_identity_incorrect…`.
- `smoke_forecast_failed` — run status `failed`; any other non-`completed` status → `run_not_terminal:<status>`.
- `provider_ledger_empty` — no ledger rows.
- `smoke_released_ledger_entry` — any ledger row with status `released` (wins over the next rule).
- `smoke_nonterminal_ledger_entry` — any ledger row whose status is not `succeeded` or `failed` (e.g. `reserved`, `running`, `pending`, `unknown`).
- `smoke_no_successful_model_request` / `smoke_no_successful_search_request` — no `succeeded` row of `provider_type` `model` / `search`. A failed attempt followed by a succeeded retry is fine.
- `smoke_no_accepted_external_evidence` — no accepted evidence. Accepted = not `rejected`, `as_of_eligible`, http(s) URL with a real external host (not the fixtures host, localhost/loopback, `*.forecastlab.local`).
- `lifetime_cost_exceeds_ceiling` — run total cost above `profile.max_estimated_cost_usd`.
- `smoke_forecast_version_missing` — no `ForecastVersion` for the run.
- probability problems via `_require_valid_probability(version.ensemble_probability)`.

Returned payload keys: `run_id`, `status`, `probability`, `model_provider`, `search_provider`, `model_cost_usd`, `search_cost_usd`, `failed_attempt_cost_usd`, `total_cost_usd`, `cost_source`, `run_attempt_count`, `provider_request_count`, `accepted_external_evidence_count` (number of accepted evidence **rows**, duplicates counted), `accepted_evidence_urls` (sorted, de-duplicated URLs of accepted rows), `ledger` (list of dicts, one per ledger row, including at least `stage`, `provider_type`, `provider`, `physical_attempt_number`, `status`, token and cost fields), `checked_at` (ISO timestamp).

### `_require_valid_probability(value) -> float`
`None` → `RuntimeError("smoke_probability_missing")`. Non-numeric, non-finite (nan/±inf), or outside [0.01, 0.99] (so 0.0, 1.0, -0.1, 1.1 all fail) → `RuntimeError("smoke_probability_invalid")`.

### `print_result(payload)`
Prints `key=value` lines to stdout for `run_id`, `status`, `probability`, `model_provider`, `search_provider`, `model_cost_usd`, `search_cost_usd`, `failed_attempt_cost_usd`, `total_cost_usd`, `cost_source`, `run_attempt_count`, `provider_request_count`, `accepted_external_evidence_count`, one `accepted_evidence_url=<url>` line per accepted URL, `checked_at`, then a line `ledger:` followed by one JSON object per ledger row (one per line). The ledger block must be the last thing printed; every non-empty line after `ledger:` must parse as a JSON object.

## 2. Experiment spend in `experiment_summary` (`apps/api/forecastlab_api/experiments.py`)

`experiment_summary(...)` must include a `"spend"` dict with `total_cost_usd`, `total_full_cost_usd`, `total_partial_cost_usd`, `total_failed_task_cost_usd`, `mean_cost_per_started_task`, `model_cost_usd`, `search_cost_usd`, `failed_attempt_cost_usd`. Each `BenchmarkResult` contributes the cost of the `ForecastRun` linked to its benchmark task (its `total_cost_usd`, else `cost_usd`), or the result's own `cost_usd` when no run exists, into the failed / partial / full bucket according to the result's flags. Runs for tasks that have no result count as failed-task spend. `total_cost_usd` is the sum of run costs, falling back to the sum of the buckets when there are no run costs. `mean_cost_per_started_task` is total divided by `experiment.total_tasks` (or the number of tasks when that is unset/zero), `None` if there are none. Example: results with no linked runs costing 1.0 (full) ×3, 1.0 (partial) ×1 and 2.0 (failed) ×1 over 5 tasks → full 3.0, partial 1.0, failed 2.0, total 6.0, mean 1.2.

Also, non-synthetic experiments should require a Python lockfile (`forecastlab.environment.require_python_lock`) when created and refuse to run (`ExperimentEnvironmentMismatch("python_lockfile_required")`) if the frozen or current environment lacks a dependency hash.

Keep existing behaviour and tests passing.
