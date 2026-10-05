# Releases lose the autopilot's pause reason and can override an owner pause

A cloud release calls `release_pause` (repeatedly, until the worker is drained) and then
`release_complete`, both in `apps/api/forecastlab_api/autopilot_routes.py`. That
round-trip mishandles the autopilot state:

1. If the autopilot was already disabled with a reason (for example
   `"Awaiting live source qualification"`), the release replaces that reason with
   `"Release maintenance"` and never puts it back.
2. If the owner pauses the autopilot during the maintenance window (pause reason
   `"Paused by owner"`), completing the release can re-enable automatic spending or
   overwrite the owner's reason.

Expected behaviour:

- Calling `release_pause` more than once must not lose what the state was before the
  first call.
- If the autopilot was enabled before the release, `release_complete` re-enables it with
  an empty pause reason only when `autopilot.enable_gaps(db)` returns no gaps. Otherwise
  it stays disabled with `pause_reason` set to the gaps joined by `"; "`.
- If it was disabled before the release, it stays disabled and gets its original pause
  reason back. Calling `release_complete` again keeps that reason.
- The owner may pause the autopilot during the maintenance window (pause reason
  `"Paused by owner"`), and the release may keep calling `release_pause` after that.
  Those later calls must not replace the owner's reason with `"Release maintenance"`.
  `release_complete` then leaves the autopilot disabled with `"Paused by owner"`, even if
  it was enabled before the release and there are no gaps.
- `release_complete` still reports `automatic_spending_enabled`.

Keep existing behaviour and tests passing.
