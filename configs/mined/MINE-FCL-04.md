# Graph generation failures don't say why they failed

When forecast graph generation fails, the API returns HTTP 422 from
`forecast_graph_error_handler` (apps/api/forecastlab_api/main.py) with only `detail` and
`reasons`. The `ForecastGraphError` carries an `audit` dict with the diagnostics a user
needs, such as which provider/model/schema was used and the schema and domain validation
errors. All of that is dropped, so a user who sees `graph_domain_validation_failed`
cannot tell what was wrong.

Expected behaviour:

- The 422 body keeps `detail` and `reasons`, and adds an `audit` object with the
  sanitized diagnostics from `exc.audit`.
- Treat this as a security boundary and only pass through known diagnostic keys (an
  allowlist). It must cover at least the provider and model identity, prompt/schema and
  transport identifiers, token-budget numbers, `schema_validation_errors` and
  `domain_validation_errors`.
- Anything else must never reach the client: raw model content, prompts, headers or
  credentials (for example `raw_content` or `authorization` keys in the audit).

Keep existing behaviour and tests passing.
