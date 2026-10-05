The in-process TTL cache in `src/ttl_cache.py` expires entries incorrectly at the exact TTL boundary.

Required behavior:

- `set(key, value)` stores a value that remains valid for `ttl_seconds`.
- An entry is expired when `now >= created_at + ttl_seconds` and `get` must return the default, deleting the stale entry.
- An entry is still valid when `now` is strictly less than the expiry instant.
- `contains` must use the same expiry rule as `get`.

Do not add dependencies. Keep the injectable `clock` argument so tests can freeze time.
