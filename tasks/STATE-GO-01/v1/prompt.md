`store.go` is a versioned key-value store. Writes currently ignore `ExpectedVersion`, so a stale writer can clobber a newer value.

Required behavior:

- `Get` returns the value and current version (starting at 0 for missing keys).
- `Put(key, value, expectedVersion)` succeeds only when `expectedVersion` equals the current version, then increments the version.
- First insert uses `expectedVersion == 0`.
- `Apply(ops)` applies a recorded schedule in order and must use the same version checks. Failed ops are recorded as rejected and must not mutate state.

Keep the API in package `state`.
