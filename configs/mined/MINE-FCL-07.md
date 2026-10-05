# OpenAI GPT-5 and o-series requests fail with HTTP 400

`OpenAICompatibleProvider.complete_json` (packages/forecasting/forecastlab/providers/openai_compatible.py)
always sends `temperature` and caps output with `max_tokens`. OpenAI's GPT-5 and
reasoning models (`o1`, `o3`, `o4` families) reject both on Chat Completions, so every
request to `gpt-5-mini-2025-08-07`, `o3-mini` and similar fails with HTTP 400.

Expected behaviour:

- When the provider id is `openai` and the model name starts with `gpt-5`, `o1`, `o3` or
  `o4` (ignore case and surrounding whitespace), send the output limit as
  `max_completion_tokens` (no `max_tokens`) and omit `temperature`.
- Every other provider keeps sending `max_tokens` and `temperature`. That includes
  `xai` and the generic `openai_compatible` provider, even when the model name looks like
  `gpt-5-...`.
- The request body otherwise stays the same (for example
  `response_format: {"type": "json_object"}`), and JSON parsing of the response is
  unchanged.
- An HTTP 400 is still a permanent error (`PermanentProviderError`, message containing
  `HTTP 400`) and is not retried.

Keep existing behaviour and tests passing.
