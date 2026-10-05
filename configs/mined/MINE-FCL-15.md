# Cursor procedural-review calls fail on the untrusted isolated workspace

`invoke_cursor_role` (scripts/run_native_v3_procedural_review.py) runs the Cursor CLI headless
(`--print ... --workspace <fresh transport workspace> ...`) against a freshly created,
isolated transport workspace. That workspace contains only the role's `manifest.json`.
Cursor's CLI asks for workspace trust in non-interactive mode, so every review invocation
stalls or exits without a usable artifact.

Expected behaviour:

- The Cursor command passed to `subprocess.run` includes the `--trust` flag, so the isolated
  review workspace is trusted explicitly.
- Do not use broader auto-approval flags: the command must not contain `--yolo` or `-f`.
- The rest of the invocation is unchanged. It still runs in ask mode with the sandbox
  enabled, the `--workspace` argument still points at the isolated transport workspace, and
  that workspace still contains only `manifest.json`.

Keep existing behaviour and tests passing.
