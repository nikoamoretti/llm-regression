# Allow creating a reviewed Forecast Contract without a model call

The only way to create a Forecast Contract through the API is
`POST /api/contracts/generate` (apps/api/forecastlab_api/main.py), which always builds a
question compiler (`build_question_compiler`) and asks a model to draft the contract. Users
who have already written the resolution terms cannot enter them directly, and every contract
costs a model call.

Add a model-free intake endpoint.

Expected behaviour:

- `POST /api/contracts/manual` accepts a JSON body with these fields:
  - required: `question`, `yes_condition`, `no_condition`, `resolution_date` (ISO
    datetime), `authoritative_source`, `resolution_method`;
  - optional: `normalized_question` (defaults to `question`), `fallback_sources`,
    `ambiguity_notes`, `cancellation_conditions`, `resolver_risk_notes`, `geography`,
    `units`, `domain`, `initial_reference_class`, `suggested_drivers`,
    `known_dependencies`, `mode` (default `"demo"`; one of `demo`, `live`, `backtest`,
    otherwise 422), `profile_id`, `as_of` and `created_by`.
- The endpoint must not call `build_question_compiler` or any model. It creates a draft
  question record (with the requested mode, profile and as-of) and a draft Forecast Contract
  linked to it. It returns the contract in the same JSON shape as
  `/api/contracts/generate`, with `status == "draft"` and a non-empty `question_id`. Trim
  surrounding whitespace from text fields, and reject a contract missing required
  resolution fields with the existing Forecast Contract error handling.
- The returned contract can be approved with the existing
  `POST /api/contracts/{id}/approve`, which responds 200 with `status == "approved"`.
- Example body that must succeed with `mode: "live"` and `profile_id: "graph_live_smoke_v1"`:
  question "Will the official indicator reach 4.5 percent?", yes/no conditions, a
  `resolution_date` of `2026-11-06T13:30:00Z`, an `authoritative_source` URL and a
  `resolution_method`. The endpoint does not check whether `resolution_date` is in the future.

Keep existing behaviour and tests passing.
