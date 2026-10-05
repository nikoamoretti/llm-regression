# The node forecast schema is rejected when a stance has no eligible claims

`forecast_node_json_schema(supporting_claim_ids=..., opposing_claim_ids=...)` builds the
strict JSON schema for a node forecast. When one side has no eligible claim ids (for
example `opposing_claim_ids=[]`), that property becomes `{"type": "array", "maxItems": 0}`
with no `items` schema. OpenAI's strict structured-output subset requires every array to
declare `items`, so the request is rejected before the model runs.

Expected behaviour: an empty side still allows only an empty array (`maxItems` 0), but
it declares an `items` schema: exactly `{"type": "string", "enum": ["__no_eligible_claim_id__"]}`.
The sentinel is unreachable, so no real claim id can ever validate.
Non-empty sides are unchanged.

Keep existing behaviour and tests passing.
