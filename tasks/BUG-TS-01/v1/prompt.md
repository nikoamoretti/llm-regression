`src/async-store.ts` loads values with overlapping async fetchers. A slower older request currently overwrites a newer result, leaving stale state.

Required behavior:

- Only the latest in-flight `load` may publish to `current()`.
- Earlier requests that settle later must not replace a newer value.
- `load` still resolves to the value produced by its own fetcher (even if that value is not published).
- A subsequent `load` after all prior requests have settled becomes the new latest request.

Do not add dependencies. Keep the file TypeScript-compatible.
