# Node forecast schema uses `uniqueItems`, which OpenAI strict schemas reject

`forecast_node_json_schema(*, supporting_claim_ids, opposing_claim_ids)` in
packages/forecasting/forecastlab/structured_outputs.py adds `"uniqueItems": true` to the
`supporting_claim_ids` and `opposing_claim_ids` array schemas. That keyword is not in
OpenAI's structured-output subset, so strict node-forecast requests fail.

Expected behaviour:

- Neither `properties.supporting_claim_ids` nor `properties.opposing_claim_ids` contains a
  `uniqueItems` key.
- Each claim-id array still limits its items to the supplied ids. For
  `supporting_claim_ids=["support"]`, `opposing_claim_ids=["oppose"]`, the schemas'
  `items.enum` are `["support"]` and `["oppose"]`. Keep the existing `maxItems` limits and the
  empty-list behaviour.

Keep existing behaviour and tests passing.
