CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE task_suites (
    suite_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    git_sha TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (name, version),
    UNIQUE (manifest_sha256)
);

CREATE TABLE tasks (
    task_version_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    suite_id UUID NOT NULL
        REFERENCES task_suites(suite_id)
        ON DELETE RESTRICT,

    task_key TEXT NOT NULL,
    task_version TEXT NOT NULL,

    category TEXT NOT NULL,
    language TEXT,
    difficulty TEXT,

    prompt_sha256 TEXT NOT NULL,
    fixture_sha256 TEXT NOT NULL,
    grader_sha256 TEXT NOT NULL,

    container_image_digest TEXT NOT NULL,

    context_class TEXT,
    manifest JSONB NOT NULL,

    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (suite_id, task_key, task_version)
);

CREATE TABLE model_configs (
    config_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    provider TEXT NOT NULL,
    request_model TEXT NOT NULL,
    reasoning_effort TEXT NOT NULL,

    temperature NUMERIC,
    top_p NUMERIC,
    max_output_tokens INTEGER,

    system_prompt_sha256 TEXT,
    tools_sha256 TEXT,

    config JSONB NOT NULL,
    config_sha256 TEXT NOT NULL UNIQUE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CHECK (
        reasoning_effort IN ('low', 'medium', 'high', 'xhigh')
    )
);

CREATE TABLE eval_runs (
    run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    suite_id UUID NOT NULL
        REFERENCES task_suites(suite_id),

    config_id UUID NOT NULL
        REFERENCES model_configs(config_id),

    baseline_run_id UUID
        REFERENCES eval_runs(run_id),

    trigger TEXT NOT NULL,
    status TEXT NOT NULL,

    runner_git_sha TEXT NOT NULL,
    runner_image_digest TEXT,

    environment JSONB NOT NULL DEFAULT '{}'::jsonb,

    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,

    notes TEXT
);

CREATE TABLE attempts (
    attempt_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    run_id UUID NOT NULL
        REFERENCES eval_runs(run_id)
        ON DELETE CASCADE,

    task_version_id UUID NOT NULL
        REFERENCES tasks(task_version_id),

    trial_index INTEGER NOT NULL,

    logical_attempt_key TEXT NOT NULL,

    requested_model TEXT NOT NULL,
    resolved_model TEXT,

    api_response_id TEXT,

    status TEXT NOT NULL,

    retry_count INTEGER NOT NULL DEFAULT 0,

    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,

    api_latency_ms BIGINT,
    wall_time_ms BIGINT,
    tool_time_ms BIGINT,
    grader_time_ms BIGINT,

    input_tokens BIGINT,
    cached_input_tokens BIGINT,
    output_tokens BIGINT,
    reasoning_tokens BIGINT,
    total_tokens BIGINT,

    cost_usd_ticks BIGINT,

    x_zero_data_retention BOOLEAN,

    response_sha256 TEXT,
    workspace_diff_sha256 TEXT,

    response_artifact_uri TEXT,
    trace_artifact_uri TEXT,
    diff_artifact_uri TEXT,

    exit_code INTEGER,

    error_code TEXT,
    error JSONB,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (
        run_id,
        task_version_id,
        trial_index
    ),

    UNIQUE (logical_attempt_key)
);

CREATE TABLE scores (
    attempt_id UUID PRIMARY KEY
        REFERENCES attempts(attempt_id)
        ON DELETE CASCADE,

    strict_pass BOOLEAN NOT NULL,

    functional_score NUMERIC NOT NULL,
    regression_score NUMERIC NOT NULL,
    constraints_score NUMERIC NOT NULL,
    partial_score NUMERIC NOT NULL,

    tests_passed INTEGER,
    tests_total INTEGER,

    forbidden_change_count INTEGER NOT NULL DEFAULT 0,

    grading_details JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CHECK (functional_score BETWEEN 0 AND 1),
    CHECK (regression_score BETWEEN 0 AND 1),
    CHECK (constraints_score BETWEEN 0 AND 1),
    CHECK (partial_score BETWEEN 0 AND 1)
);

CREATE TABLE test_results (
    attempt_id UUID NOT NULL
        REFERENCES attempts(attempt_id)
        ON DELETE CASCADE,

    test_case_id TEXT NOT NULL,
    test_group TEXT NOT NULL,

    passed BOOLEAN NOT NULL,
    weight NUMERIC NOT NULL DEFAULT 1,

    duration_ms BIGINT,
    failure_signature TEXT,

    details JSONB NOT NULL DEFAULT '{}'::jsonb,

    PRIMARY KEY (attempt_id, test_case_id)
);

CREATE TABLE events (
    event_id BIGSERIAL PRIMARY KEY,

    attempt_id UUID NOT NULL
        REFERENCES attempts(attempt_id)
        ON DELETE CASCADE,

    seq INTEGER NOT NULL,
    event_time TIMESTAMPTZ NOT NULL,

    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,

    UNIQUE (attempt_id, seq)
);

CREATE TABLE alerts (
    alert_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    config_id UUID NOT NULL
        REFERENCES model_configs(config_id),

    metric TEXT NOT NULL,
    severity TEXT NOT NULL,

    baseline_window JSONB NOT NULL,
    current_window JSONB NOT NULL,

    baseline_value NUMERIC,
    current_value NUMERIC,
    absolute_delta NUMERIC,
    relative_delta NUMERIC,

    ci_low NUMERIC,
    ci_high NUMERIC,

    decision_rule TEXT NOT NULL,

    status TEXT NOT NULL DEFAULT 'open',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_attempts_run
    ON attempts(run_id);

CREATE INDEX idx_attempts_task
    ON attempts(task_version_id);

CREATE INDEX idx_attempts_completed
    ON attempts(completed_at);

CREATE INDEX idx_events_attempt
    ON events(attempt_id, seq);

CREATE INDEX idx_scores_pass
    ON scores(strict_pass);
