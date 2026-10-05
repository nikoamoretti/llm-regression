# Add OpenRouter as a model provider

ForecastLab can only reach models directly through OpenAI, xAI or a generic
OpenAI-compatible endpoint. We want to be able to use OpenRouter
(`FORECASTLAB_MODEL_PROVIDER=openrouter`, models addressed like `openai/gpt-5-mini` or
`anthropic/claude-sonnet-4.5`). Right now `openrouter` is not accepted as a live provider,
there is no default base URL for it, and OpenAI models reached through OpenRouter lose the
strict-schema and GPT-5 request handling that direct OpenAI calls get.

Relevant code: packages/forecasting/forecastlab/execution.py,
packages/forecasting/forecastlab/providers/factory.py,
packages/forecasting/forecastlab/providers/openai_compatible.py and
configs/pricing/models.yaml.

Expected behaviour:

- `resolve_execution_context(requested_mode="live", ...)` with `model_provider="openrouter"`
  (plus a model key, the Tavily search provider and key) resolves to
  `effective_mode == "live"` and `model_provider == "openrouter"`.
- `forecastlab.providers.factory` exposes `default_base_url(provider: str) -> str`:
  `"https://openrouter.ai/api/v1"` for `openrouter`, `"https://api.x.ai/v1"` for `xai`, and
  `"https://api.openai.com/v1"` for anything else. `build_model_provider(provider="openrouter",
  api_key=..., base_url=None, model=..., timeout=..., execution=<live context>)` returns an
  `OpenAICompatibleProvider` with `base_url == "https://openrouter.ai/api/v1"` and
  `provider_id == "openrouter"`. When an execution context is given and no base URL is set,
  use the provider's own default, not OpenAI's.
- For `provider_id="openrouter"` with a model name starting `openai/` (ignore case and
  surrounding whitespace), the part after `openai/` gets the same model-family rules as a
  direct `openai` provider. These cover the strict `json_schema` response format,
  `max_completion_tokens` instead of `max_tokens`, no `temperature`, and `verbosity`, with
  two differences:
  - send the reasoning effort as `"reasoning": {"effort": <effort>}`, not
    `reasoning_effort`;
  - whenever a strict schema is sent, also send `"provider": {"require_parameters": true}`.
  For `openai/gpt-5-mini` with schema `track_forecast`, a schema, `reasoning_effort="minimal"`,
  `verbosity="low"` and `max_output_tokens=4096`, the POST goes to
  `https://openrouter.ai/api/v1/chat/completions` with `model` `"openai/gpt-5-mini"`,
  `response_format` `{"type": "json_schema", "json_schema": {"name": "track_forecast",
  "strict": true, "schema": <schema>}}`, `reasoning` `{"effort": "minimal"}`, `verbosity`
  `"low"`, `max_completion_tokens` 4096, and no `reasoning_effort`, `max_tokens` or
  `temperature`.
- Other OpenRouter vendors (for example `anthropic/claude-sonnet-4.5`) keep JSON-object mode
  (`{"type": "json_object"}`), `max_tokens` and `temperature` 0.2, with no `provider` and no
  `reasoning` key.
- Cost: when an OpenRouter response's `usage.cost` is a non-negative number (not a bool),
  record it as `cost_usd` with `cost_source == "provider_reported"`. Otherwise use the
  catalog estimate (`cost_source == "estimated"`). Add an `openrouter` entry to the pricing
  catalog whose `unknown` rate is `input_per_million: 5.0`, `output_per_million: 15.0`, so
  1,000,000 prompt tokens and 0 completion tokens cost 5.0.
- Direct `openai` requests are unchanged: they still send `reasoning_effort`, never send
  `reasoning` or `provider`, and ignore any `usage.cost` in the response (cost stays
  `estimated`).

Keep existing behaviour and tests passing.
