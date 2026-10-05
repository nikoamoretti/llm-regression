# Three-track estimates fail with invalid_structured_output:track_forecast; add a strict-schema three-track profile

The three-track forecaster's per-track estimate (`schema_name="track_forecast"`, validated as
`TrackForecastOutput`) is requested in plain JSON-object mode with no schema. Models often
return values such as `"direction": "positive"` for a driver, so the estimate fails
validation. The single repair request then only re-sends the bad JSON, so runs end with
`invalid_structured_output:track_forecast`.

`three_track_forecaster` is a frozen comparator, so its requests must not change. Add a new
profile that sends strict track estimates. Relevant code:
packages/forecasting/forecastlab/engine.py, packages/forecasting/forecastlab/structured_outputs.py,
packages/forecasting/forecastlab/providers/openai_compatible.py and configs/forecast_profiles/.

Expected behaviour:

- New profile `three_track_strict_forecaster_v1`, loadable with `load_profile`. Apart from
  `id`, `label` and `description`, its `model_dump()` must equal that of
  `three_track_forecaster`.
- `forecastlab.engine.uses_strict_track_forecast(profile) -> bool` is True only for
  `three_track_strict_forecaster_v1`. It is False for `three_track_forecaster`,
  `three_track_ensemble` and every other profile.
- `forecastlab.structured_outputs.track_forecast_json_schema(*, evidence_ids: list[str]) -> dict`
  returns a JSON schema that mirrors `TrackForecastOutput` and fits the OpenAI strict subset.
  Every object schema has `additionalProperties: false` and lists all of its properties in
  `required`, and every array schema has an `items` schema. Under it:
  - `properties.key_drivers.items.properties.direction.enum` is `["up", "down", "unclear"]`;
  - `properties.key_drivers.items.properties.evidence_ids.items.enum` is the given
    `evidence_ids`, in order;
  - when `evidence_ids` is empty, that `evidence_ids` array schema has `maxItems: 0` and
    still has an `items` schema.
- For the strict profile only, each track estimate call to `model.complete_json(...)` passes
  `json_schema=track_forecast_json_schema(evidence_ids=[...])`. The list holds the ids of the
  evidence records gathered for that track, in the same order as that track's `evidence`.
  Legacy profiles (`three_track_forecaster`, `three_track_ensemble`, ...) must not pass a
  `json_schema` keyword at all on any call.
- Repair (strict profile only): when the first track estimate fails validation, the repair
  request's `user` text still contains the existing `Previous JSON failed validation. Return
  corrected JSON only.` text. It also contains `Validation errors:` followed by the sanitized
  validation errors (field paths such as `key_drivers.0.direction`). The repair request sends
  the same `json_schema` as the first request. Legacy profiles' repair text is unchanged and
  does not contain `Validation errors:`. Two invalid responses in a row still raise
  `StructuredOutputError` matching `invalid_structured_output:track_forecast`.
- `validate_structured_output("track_forecast", payload)` validates against
  `TrackForecastOutput`. Return `(None, errors)` with sanitized errors (for a bad driver
  direction, the only error's `field_path` is `key_drivers.0.direction`, and the rejected
  value does not appear anywhere in the errors). Return `(validated_dict, [])` for a valid
  payload.
- `OpenAICompatibleProvider.complete_json` treats `track_forecast` as a strict structured
  task. For `provider_id="openai"` with `gpt-5-mini-2025-08-07` and a `json_schema`, it sends
  `response_format` `{"type": "json_schema", "json_schema": {"name": "track_forecast",
  "strict": true, "schema": <schema>}}`, and the result diagnostics report
  `strict_schema_validation_succeeded` according to validation (False for a response that
  is only `{"probability": 0.61}`). For `xai` / `grok-4` and for `openai_compatible`, it keeps
  `{"type": "json_object"}`. No `reasoning_effort` is sent unless one is requested.

Keep existing behaviour and tests passing.
