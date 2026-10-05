The locally vendored SDK in `vendor/sdk.py` changed. Application code in `src/client.py` still calls the old API and is broken.

New SDK contract:

- `Client.get_item(item_id: str, fields: tuple[str, ...] = ("id", "name"))`
- The old `Client.fetch(id)` helper was removed.

Adapt only application code. Do not modify `vendor/`. Keep returning a dict with at least `id` and `name`.
