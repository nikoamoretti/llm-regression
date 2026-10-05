# Live graph runs have no budget-sized profile, and preflight ignores search cost

Launch preflight (`resolve_execution_context` in
`packages/forecasting/forecastlab/execution.py`) estimates the upper-bound cost of a run
from model pricing only. Planned search calls are ignored, so the estimate is too low.
When pricing is missing, the run goes ahead with an unknown cost. When preflight does
refuse a run, the error just says "exceeds the Settings cost ceiling" with no numbers, and
the API response has no structured breakdown. There is also no graph profile small enough
to smoke-test the full graph pipeline live under a $0.50 ceiling: `graph_forecaster_v1`
always fails preflight.

Expected behaviour:

1. **New profile** `configs/forecast_profiles/graph_live_smoke_v1.yaml` with exactly these
   settings:

   ```yaml
   id: graph_live_smoke_v1
   version: 1
   label: Graph live smoke V1
   description: Tightly bounded live execution profile that exercises the complete graph forecasting path. It is not an evaluation or production-quality profile.
   execution_strategy: graph_nodes
   graph_generation_enabled: true
   evidence_claims_enabled: true
   node_forecasting_enabled: true
   graph_aggregation_enabled: true
   tracks:
     - single_agent
   subquestions_per_track: 1
   search_results_per_subquestion: 2
   fetches_per_subquestion: 1
   aggregation_method: importance_weighted_log_odds_v1
   shrinkage: 0.0
   max_model_calls: 14
   max_search_calls: 4
   max_fetched_documents: 8
   max_tokens: 50000
   max_output_tokens_per_call: 1536
   max_estimated_cost_usd: 0.50
   max_wall_clock_seconds: 300
   prompt_versions:
     forecast_graph: v1
     graph_research: v1
     evidence_claims: v1
     forecast_node: v3
   ```

   Do not change `graph_forecaster_v1.yaml` at all. It must stay byte-for-byte identical.
   The new profile must not be added to `CONTROLLED_FORECAST_PROFILES`,
   `V1_EVALUATION_PROFILES` or `DEFAULT_EXPERIMENT_PROFILES`. A demo-mode run with this
   profile must complete through the normal graph pipeline.

2. **Cost breakdown on `ExecutionContext`.** Add these fields:
   - `estimated_model_upper_bound_cost_usd: float | None`
   - `estimated_search_upper_bound_cost_usd: float | None`
   - `model_cost_estimate_label: str`, `search_cost_estimate_label: str` and
     `cost_estimate_label: str` (each defaults to `"unavailable"`)
   - `cost_estimate_unavailable_reasons: list[str]`

   How the values are computed:
   - The model estimate is the existing `estimate_cost(...)` result.
   - The search estimate is the planned search calls (from `estimate_workload`) multiplied
     by the per-request cost from `forecastlab.pricing.estimate_search_cost`. It is `0.0`
     when no searches are planned. Call it as `estimate_search_cost(search_provider)` with
     a single positional argument, through the name imported into
     `forecastlab.execution`, so that patching `forecastlab.execution.estimate_search_cost`
     takes effect.
   - `estimated_upper_bound_cost_usd` is model plus search, rounded to 6 decimals. It is
     `None` if either part is unavailable. The ceiling comparison
     (`estimate_exceeds_ceiling`) uses this combined total.
   - `cost_estimate_unavailable_reasons` contains `"model_pricing_unavailable"` and/or
     `"search_pricing_unavailable"` (searches planned but no per-request price).
   - `cost_estimate_label` is `"unavailable"` when any reason is present. Otherwise it is
     the existing `combine_cost_labels` of the model and search labels.

   With live settings `model_provider=openai`, `model_name=gpt-5-mini-2025-08-07` and
   `search_provider=tavily`, the existing pricing catalog should give:
   - `graph_live_smoke_v1`: model 0.400, search 0.032, total 0.432.
   - `graph_forecaster_v1`: model 1.600, search 0.288, total 1.888.

   In demo mode with mock providers, all three estimates are `0.0`.

3. **Fail closed with details.** `ConfigurationError` (`packages/forecasting/forecastlab/errors.py`)
   gains a keyword-only `details: dict | None = None` argument, stored as `.details`.
   `resolve_execution_context` raises it in two cases:
   - If any pricing is unavailable, raise with `reasons` equal to the unavailable reasons
     (for example `["search_pricing_unavailable"]`). This happens regardless of the ceiling.
   - If the estimate exceeds the effective ceiling (and over-ceiling is not allowed), raise
     with `reasons == ["estimate_exceeds_cost_ceiling"]`.

   In both cases `details` is a dict with keys `profile_id`,
   `estimated_model_upper_bound_cost_usd`, `estimated_search_upper_bound_cost_usd`,
   `estimated_upper_bound_cost_usd`, `effective_max_cost_usd`,
   `model_cost_estimate_label`, `search_cost_estimate_label`, `cost_estimate_label`,
   `cost_estimate_unavailable_reasons` and `estimate_exceeds_ceiling`. The error message
   names the profile id and includes
   `model=<m>, search=<s>, total=<t>, ceiling=$<c>`. Each amount is formatted as `$` with
   6 decimals (for example `model=$0.400000`, `ceiling=$0.250000`), or the word
   `unavailable` when it is `None` (for example `search=unavailable`,
   `total=unavailable`).

4. **API** (`apps/api/forecastlab_api/main.py`):
   - The `ConfigurationError` 422 handler adds `"preflight": exc.details` to the JSON body
     when details are present. Creating a live run of `graph_live_smoke_v1` with a $0.25
     Settings ceiling therefore returns 422 with
     `reasons == ["estimate_exceeds_cost_ceiling"]`, the formatted `detail`, and
     `preflight.estimated_upper_bound_cost_usd == 0.432`. No run is created and no
     provider is constructed.
   - `GET /api/execution/preview` also adds `"preflight"` when the error carries details.
     With a $0.50 ceiling it reports `ready: true`, and the context includes the new cost
     fields.

Keep existing behaviour and tests passing.
