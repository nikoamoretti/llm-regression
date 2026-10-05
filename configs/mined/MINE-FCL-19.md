# Reviewed cloud release: stop importing redacted settings, deploy via project-scoped Vercel REST, and keep autopilot pause reasons intact

Our reviewed release (`scripts/release_cloud.py`, run from `.github/workflows/release.yml`) has three related problems.

1. **Redacted settings leak into config.** The release pulls the whole production environment through `vercel env pull` and copies every value into `os.environ`. Vercel exports sensitive provider settings as the literal string `[SENSITIVE]`, so things like `FORECASTLAB_ALLOW_LOCAL_FIXTURES` or `FORECASTLAB_MAX_COST_USD` end up as garbage booleans/numbers, and provider keys are imported into a process that never needs them.
2. **The Vercel CLI cannot be used with project-scoped tokens** (it performs an account/scope lookup our tokens are not allowed to do). The release must talk to the Vercel REST API directly, scoped to a project and team.
3. **Autopilot pause reasons are lost across a release.** Pausing for release maintenance overwrites the owner-visible `pause_reason` with `"Release maintenance"`, and completing the release never restores it. An owner pause made during maintenance can also be clobbered by the next pause poll.

## Required behaviour

Make `scripts` an importable package (`scripts/__init__.py`) so tests can `from scripts.release_cloud import …` and `from scripts.vercel_release import …`; the workflow should run the release as `python -m scripts.release_cloud`. The release must no longer need the Vercel CLI.

### `scripts.release_cloud.configure_release_environment(values) -> sqlalchemy URL`
`values` is a mapping of production environment variables.
- It requires readable `DATABASE_URL_UNPOOLED` and `BLOB_READ_WRITE_TOKEN`. If either is missing/empty or equals `"[SENSITIVE]"`, raise `RuntimeError("release_credential_unavailable:<KEY>")` (checked in that order) **without modifying `os.environ`** at all.
- Otherwise it updates `os.environ` (look it up at call time; tests replace it with a plain dict) with only what the release needs: `FORECASTLAB_ENV=production`, `FORECASTLAB_ALLOW_LOCAL_FIXTURES=false`, `FORECASTLAB_EMBEDDED_WORKER=false`, `FORECASTLAB_BLOB_TOKEN=<blob token>`, `FORECASTLAB_DATABASE_URL=<database url with the psycopg driver, password included>`. No other value from `values` (e.g. `FORECASTLAB_MODEL_API_KEY`, `FORECASTLAB_MAX_COST_USD`) may be copied into the environment.
- It returns the database URL (`sqlalchemy.engine.URL`) with `drivername == "postgresql+psycopg"`.

`main()` should obtain the values from the API project's REST environment (below) and use this function instead of pulling a dotenv file.

### New module `scripts/vercel_release.py`

`included(name: str, *, web: bool) -> bool` decides whether a committed repo path goes into a deployment bundle:
- Never include absolute paths, paths containing `..`, or any path with a component starting with `.env` (e.g. `.env`, `apps/api/.env.production`, `apps/web/.env.local`).
- `web=True`: only files under `apps/web/`, excluding `apps/web/e2e/`. E.g. `apps/web/app/page.tsx` is included; `apps/api/forecastlab_api/main.py` and `apps/web/e2e/autopilot.spec.ts` are not.
- `web=False` (API bundle): never `data/local/…`, never `apps/web/…`, never database files (`*.db`, `.sqlite`), never tests (e.g. `tests/test_api.py` is excluded). Include the API runtime: `app.py`, `vercel.json`, `pyproject.toml`, `uv.lock`, `README.md`, `alembic.ini`, and anything under `apps/api/`, `packages/`, `configs/`, `prompts/`, `alembic/`, `data/`. So `data/local/credentials.json` and `data/forecastlab.db` are excluded.

`class VercelProject(project_id: str, token: str, team_id: str)`:
- Has a `client` attribute: an `httpx.Client` against `https://api.vercel.com` with a bearer-token header (tests call `project.client.close()`).
- `request(path, *, method="GET", body=None, params=None)` sends to the API with `teamId=<team_id>` added to the query, raises `RuntimeError("vercel_api_failed:<method>:<path>:<status>")` on status ≥ 300, and returns the decoded JSON (or `{}` when the body is empty). Other methods must go through `self.request` (tests replace it on the instance).
- `environment() -> dict[str, str]`: lists the project's env vars via a path ending in `/env` (response `{"envs": [{"id", "key", "target": [...]}, ...]}`), keeps only `DATABASE_URL_UNPOOLED` and `BLOB_READ_WRITE_TOKEN` entries whose `target` includes `"production"` (preview-only entries are ignored and must not be fetched), and fetches each kept entry's decrypted value via a path ending in `/env/<id>` (response `{"value": ..., "decrypted": bool}`). If an entry is not decrypted, raise `RuntimeError("release_credential_unavailable:<KEY>")`. Returns `{key: value}` for whichever qualifying entries exist, unvalidated (a key that is not listed is simply absent); presence and value checks happen only in `configure_release_environment`. Constructing `VercelProject` must not make any request, and `environment()` issues only the `/env` listing request and one `/env/<id>` request per kept entry, passing the path as the only positional argument to `self.request`.
- Also provide REST equivalents of what the CLI used to do: `stage(root, *, web)` (create a production-target deployment from the committed tree filtered by `included`, without moving the project's current production target, wait for `READY`), `smoke(deployment, path, expected_status)` (hit the staged `*.vercel.app` URL with the project's automation protection-bypass secret), and `promote(deployment)` (promote and confirm the production target moved). `release_cloud.main()` should use these for API and web and close both clients when done.

### Autopilot release control (`apps/api/forecastlab_api/autopilot_routes.py`)
`release_pause(db)` and `release_complete(db)` (the `/internal/release/pause` and `/internal/release/complete` handlers; tests call them directly with a session) must preserve the owner-visible state:
- On the **first** pause (dispatch not already paused), record in the `release_control` setting both the current `enabled` flag (as today) and the current `pause_reason`, then set `pause_reason = "Release maintenance"`. Repeated pause polls while already paused keep the originally recorded `was_enabled` and `pause_reason`, and must not overwrite the current `pause_reason` unless it is still `"Release maintenance"` (so a `"Paused by owner"` set via `autopilot.set_enabled(session, False)` during maintenance survives later polls). `release_pause` keeps returning `{"paused": True, "drained": ..., "running_job_ids": [...]}`.
- `release_complete` only acts on the pause state when dispatch was paused and the current `pause_reason` is still `"Release maintenance"`:
  - if autopilot was enabled before the release and `autopilot.enable_gaps(db)` is empty → re-enable with `pause_reason = ""`;
  - otherwise leave it disabled with `pause_reason` = the gaps joined by `"; "`, or else the recorded original pause reason, or else `"Autopilot remains paused"`.
  - If the owner paused during maintenance (reason is no longer `"Release maintenance"`), leave the state alone.
  It still marks dispatch unpaused and returns `{"dispatch_paused": False, "automatic_spending_enabled": <enabled>}`. Calling it again afterwards must not change the restored reason.

Examples: autopilot disabled with reason `"Awaiting live source qualification"` → pause twice (both report `drained`) → complete → still disabled, reason `"Awaiting live source qualification"` (and still so after a second complete). Enabled → pause → complete with gaps `["Method changed; qualify the new revision"]` → disabled with exactly that reason; with no gaps → enabled with reason `""`. Enabled → pause → owner pauses → pause again → complete → disabled with reason `"Paused by owner"`.

Keep existing behaviour and tests passing.
