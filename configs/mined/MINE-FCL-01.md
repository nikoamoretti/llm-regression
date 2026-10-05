# Compact graph responses with forward or cyclic references are treated as domain failures

When ForecastLab asks a model for a forecast graph in the compact transport, each node
`n[i]` refers to other nodes by array index (`p` for its parent, `d` for its
dependencies). Nothing in the transport stops a node from pointing at itself or at a
later node. A response like `n[0].d = [1]` and `n[1].d = [0]` (a cycle) gets past
transport validation and only fails later as `graph_domain_validation_failed`, which
hides the fact that the model broke the transport contract.

Expected behaviour:

- The compact transport uses a topological convention: for node `n[i]`, `p` and every
  index in `d` must be strictly smaller than `i`, so the first node has `p=null` and
  `d=[]`. Tell the model this in the compact transport system prompt's transport
  rules. The rule must appear exactly once and use the wording "strictly smaller than i".
- When a compact response breaks the convention, reject it at the transport boundary.
  Report it as a schema validation error with `error_type` `"non_topological_reference"`
  for field path `n.<index>`, so the generator fails with reason
  `structured_output_schema_invalid` and the error shows up in the audit's
  `schema_validation_errors`. It must not be retried or repaired, so the model is called once.
- Other domain failures, such as duplicate questions, are still reported as
  `graph_domain_validation_failed`.

Keep existing behaviour and tests passing.
