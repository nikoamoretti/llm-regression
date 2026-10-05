# The compact graph JSON schema allows dangling relationship indexes

`compact_forecast_graph_json_schema` (packages/forecasting/forecastlab/structured_outputs.py)
describes every node of the compact array `n` with the same item schema. Relationship
indexes (`p`, and the entries of `d`) are only "integer >= 0". The compact transport
convention is topological: node `n[i]` may refer only to indexes strictly smaller than
`i`. The provider's strict structured-output mode therefore accepts responses whose
indexes point past the end of the array or at later nodes, for example a nine-node
graph where node eight refers to index 9.

Expected behaviour: the schema itself enforces the convention for each array position.

- `n` describes each position separately with `prefixItems`, one schema per possible
  node (the array still allows 5 to 10 nodes). Positions beyond those are forbidden
  (`"items": false`).
- Node 0: `p` is exactly `{"type": "null"}`, and `d` allows no entries (`maxItems` 0).
- Node `i > 0`: `p` keeps its current form (`{"type": ["integer", "null"], "minimum": 0}`)
  and adds `"maximum": i - 1`. `d` has `maxItems` `min(max_dependencies_per_node, i)`,
  and its `items` is `{"type": "integer", "minimum": 0, "maximum": i - 1}`. Express the
  bounds with `minimum`/`maximum`, not `enum` or `anyOf`.
- Every other field and constraint of a node is unchanged.

Keep existing behaviour and tests passing.
