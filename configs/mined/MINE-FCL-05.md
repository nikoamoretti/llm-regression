# Cloud release imports redacted "[SENSITIVE]" settings into the application config

`scripts/release_cloud.py` pulls the production environment from Vercel and then copies
every pulled value into `os.environ` before running migrations. Vercel exports sensitive
values as the literal string `[SENSITIVE]`, so settings like
`FORECASTLAB_ALLOW_LOCAL_FIXTURES=[SENSITIVE]` or `FORECASTLAB_MAX_COST_USD=[SENSITIVE]`
reach the application's settings parsing during a release. A missing or redacted database
or Blob credential is only discovered halfway through.

Expected behaviour: add `configure_release_environment(values)` to
`scripts/release_cloud.py` and use it from `main()` to turn the pulled values into the
release environment.

- `DATABASE_URL_UNPOOLED` and `BLOB_READ_WRITE_TOKEN` are required. If either is missing,
  empty or `[SENSITIVE]`, raise `RuntimeError("release_credential_unavailable:<NAME>")`
  (for example `release_credential_unavailable:BLOB_READ_WRITE_TOKEN`) before changing
  `os.environ` at all.
- Return the database URL as a SQLAlchemy URL using the `postgresql+psycopg` driver.
- Set only what a release needs: `FORECASTLAB_ENV=production`,
  `FORECASTLAB_ALLOW_LOCAL_FIXTURES=false`, `FORECASTLAB_EMBEDDED_WORKER=false`,
  `FORECASTLAB_BLOB_TOKEN` (the Blob token) and `FORECASTLAB_DATABASE_URL` (the URL above,
  password included). Other pulled values, such as model API keys and cost limits, must
  not be exported.

Keep existing behaviour and tests passing.
