# OpenAI rejects the compact forecast-graph schema (`items: false`)

`compact_forecast_graph_json_schema` (packages/forecasting/forecastlab/structured_outputs.py)
describes the compact `n` node array with ten positional `prefixItems` schemas, `maxItems: 10`
and `"items": false`. OpenAI's strict structured-output subset requires `items` to be a schema
object, so compact graph generation requests (`forecast_graph_compact_indexed_v1`) are
rejected before any graph is produced.

Expected behaviour:

- `properties.n.items` in the compact graph schema is a strict object schema
  (`"type": "object"`, same node shape as the positional schemas) instead of `false`.
- Everything else stays as it is: exactly 10 `prefixItems`, `minItems` 5 / `maxItems` 10,
  per-position relationship-index bounds (`p` is `{"type": "null"}` at index 0, the `d`
  dependency array has `maxItems` 0 at index 0, and index 3 has a `p` maximum and `d` item
  maximum of 2), node property keys `q, t, w, p, d, s, o`, and successful restoration of the
  canonical graph.

Keep existing behaviour and tests passing.
