-- Optional PostgreSQL follow-up. SQLite remains the default system of record.
-- Applied conceptually by runner/migrations.py for SQLite.

ALTER TABLE model_configs DROP CONSTRAINT IF EXISTS model_configs_reasoning_effort_check;
ALTER TABLE model_configs ADD CONSTRAINT model_configs_reasoning_effort_check
    CHECK (reasoning_effort IN ('low', 'medium', 'high', 'xhigh', 'max', 'ultra'));

ALTER TABLE attempts ADD COLUMN IF NOT EXISTS requested_effort TEXT;
ALTER TABLE attempts ADD COLUMN IF NOT EXISTS verified_effort TEXT;
ALTER TABLE attempts ADD COLUMN IF NOT EXISTS verified_model TEXT;
ALTER TABLE attempts ADD COLUMN IF NOT EXISTS quality_status TEXT;
ALTER TABLE attempts ADD COLUMN IF NOT EXISTS track TEXT;
ALTER TABLE attempts ADD COLUMN IF NOT EXISTS client_mode TEXT;
ALTER TABLE attempts ADD COLUMN IF NOT EXISTS auth_surface TEXT;
ALTER TABLE attempts ADD COLUMN IF NOT EXISTS thread_id TEXT;
