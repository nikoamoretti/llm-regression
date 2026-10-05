Implement an in-memory token-bucket rate limiter used by the existing public API.

Rules:

- Do **not** modify `src/public-api.ts`. The public function signatures are frozen.
- Put the implementation in `src/internal/rate-limiter.ts`.
- `allow(key, cost = 1)` consumes `cost` tokens and returns true when the request is admitted.
- Buckets refill at `refillPerSecond` using the injectable clock.
- Capacity is `burst`. A new key starts full.

`src/public-api.ts` already imports `./internal/rate-limiter.ts`.
