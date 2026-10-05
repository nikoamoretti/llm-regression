# Node forecasts are requested without a schema, so models can cite claims they were not given

`NodeForecaster.forecast` (packages/forecasting/forecastlab/node_forecasting.py) calls
`model.complete_json(system=..., user=..., schema_name="forecast_node")` with no JSON schema
and no reasoning or verbosity controls. The provider falls back to free-form JSON-object
mode. Responses often cite claim ids that were never supplied, or come back malformed, and
the deterministic post-validation then rejects them.

Expected behaviour:

- New `forecastlab.structured_outputs.forecast_node_json_schema(*, supporting_claim_ids:
  list[str], opposing_claim_ids: list[str]) -> dict[str, Any]`. It returns a strict object
  schema (`additionalProperties: false`, all properties required) with these properties:
  - `probability`: number in [0, 1];
  - `reasoning`: non-empty string, at most 1200 characters;
  - `supporting_claim_ids` and `opposing_claim_ids`: arrays of strings limited (via `enum`)
    to the given ids, at most 20 items each. When no ids are given for a list, that array
    has `maxItems: 0`;
  - `uncertainty_notes`: array of at most 8 non-empty strings, each at most 300 characters.
- `NodeForecaster.forecast` passes these keyword arguments to `complete_json`:
  - `json_schema=forecast_node_json_schema(...)`, built from the eligible claims for the
    node. Supporting ids are the claims whose `supports_or_refutes == "supports"` and opposing
    ids those with `"refutes"`, each list sorted by id. For a single supporting claim `support`,
    the schema equals `forecast_node_json_schema(supporting_claim_ids=["support"],
    opposing_claim_ids=[])`;
  - `reasoning_effort="minimal"`;
  - `verbosity="low"`.
- The user context sent to the model and the resulting `NodeForecast` are unchanged.

Keep existing behaviour and tests passing.
