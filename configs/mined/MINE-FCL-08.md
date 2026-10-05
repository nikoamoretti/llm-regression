# New prospective cohorts should use the strict three-track profile, and older cohort reports must not change

Prospective cohorts (apps/api/forecastlab_api/prospective.py) assign every question to the
methods in the module-level `METHODS` tuple when the cohort is frozen. The third method is
still `three_track_forecaster`, which keeps failing with invalid track-estimate output. New
cohorts should use the strict-schema profile `three_track_strict_forecaster_v1` (it already
exists under configs/forecast_profiles/) instead.

Changing the assignment exposes a second bug. `cohort_report(session, cohort_id)` builds its
per-method summary and its "matched" set by looping over the current `METHODS` constant rather
than the methods the cohort was actually frozen with. A cohort frozen earlier with
`three_track_forecaster` would then report `three_track_strict_forecaster_v1` with zero
assignments and lose its real third method.

Expected behaviour:

- `prospective.METHODS` is exactly
  `("root_event_ensemble_v1", "single_model_forecaster_v1", "three_track_strict_forecaster_v1")`,
  and freezing a new cohort assigns and reports those three methods, in that order.
- `cohort_report` uses the `methods` list stored in the cohort's frozen manifest
  (`manifest_json`) for the order and contents of the `methods` entries in its result, and
  for deciding which questions count as fully matched (every method has a scored cell). Fall
  back to the current `METHODS` only when the manifest has no methods.
- A cohort frozen while `METHODS` held the legacy tuple
  `("root_event_ensemble_v1", "single_model_forecaster_v1", "three_track_forecaster")` still
  reports exactly those three methods, in that order, each with `assigned == 1` for a
  one-question cohort, even after `METHODS` has changed.

Keep existing behaviour and tests passing.
