The JSON user store in `src/users.py` must gain a `display_name` field.

Requirements:

- `migrate(records)` upgrades v1 records `{id, email, created_at}` to v2 records that also include `display_name`.
- Missing `display_name` defaults to the local part of `email` (text before `@`).
- `create_user` requires `display_name` for new records and writes `schema_version: 2`.
- `load_users` must migrate on read so old files keep working.
- Do not drop unknown future fields.
