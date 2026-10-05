CREATE TABLE IF NOT EXISTS task_suites (
    suite_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    git_sha TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (name, version),
    UNIQUE (manifest_sha256)
);

CREATE TABLE IF NOT EXISTS tasks (
    task_version_id TEXT PRIMARY KEY,
    suite_id TEXT NOT NULL REFERENCES task_suites(suite_id) ON DELETE RESTRICT,
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
    manifest TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (suite_id, task_key, task_version)
);

CREATE TABLE IF NOT EXISTS model_configs (
    config_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    request_model TEXT NOT NULL,
    reasoning_effort TEXT NOT NULL,
    temperature REAL,
    top_p REAL,
    max_output_tokens INTEGER,
    system_prompt_sha256 TEXT,
    tools_sha256 TEXT,
    config TEXT NOT NULL,
    config_sha256 TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (reasoning_effort IN ('low', 'medium', 'high', 'xhigh', 'max', 'ultra'))
);

CREATE TABLE IF NOT EXISTS eval_runs (
    run_id TEXT PRIMARY KEY,
    suite_id TEXT NOT NULL REFERENCES task_suites(suite_id),
    config_id TEXT NOT NULL REFERENCES model_configs(config_id),
    baseline_run_id TEXT REFERENCES eval_runs(run_id),
    trigger TEXT NOT NULL,
    status TEXT NOT NULL,
    runner_git_sha TEXT NOT NULL,
    runner_image_digest TEXT,
    environment TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    notes TEXT,
    track TEXT,
    client_mode TEXT,
    schedule_seed INTEGER,
    suite_hash TEXT,
    requested_model TEXT,
    verified_model TEXT,
    requested_effort TEXT,
    verified_effort TEXT,
    auth_surface TEXT
);

CREATE TABLE IF NOT EXISTS attempts (
    attempt_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES eval_runs(run_id) ON DELETE CASCADE,
    task_version_id TEXT NOT NULL REFERENCES tasks(task_version_id),
    trial_index INTEGER NOT NULL,
    logical_attempt_key TEXT NOT NULL,
    requested_model TEXT NOT NULL,
    resolved_model TEXT,
    api_response_id TEXT,
    status TEXT NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    api_latency_ms INTEGER,
    wall_time_ms INTEGER,
    tool_time_ms INTEGER,
    grader_time_ms INTEGER,
    input_tokens INTEGER,
    cached_input_tokens INTEGER,
    output_tokens INTEGER,
    reasoning_tokens INTEGER,
    total_tokens INTEGER,
    cost_usd_ticks INTEGER,
    x_zero_data_retention INTEGER,
    response_sha256 TEXT,
    workspace_diff_sha256 TEXT,
    response_artifact_uri TEXT,
    trace_artifact_uri TEXT,
    diff_artifact_uri TEXT,
    exit_code INTEGER,
    error_code TEXT,
    error TEXT,
    requested_effort TEXT,
    verified_effort TEXT,
    verified_model TEXT,
    quality_status TEXT,
    track TEXT,
    client_mode TEXT,
    auth_surface TEXT,
    thread_id TEXT,
    codex_version TEXT,
    codex_sha256 TEXT,
    schedule_order INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (run_id, task_version_id, trial_index),
    UNIQUE (logical_attempt_key)
);

CREATE TABLE IF NOT EXISTS scores (
    attempt_id TEXT PRIMARY KEY REFERENCES attempts(attempt_id) ON DELETE CASCADE,
    strict_pass INTEGER NOT NULL,
    functional_score REAL NOT NULL,
    regression_score REAL NOT NULL,
    constraints_score REAL NOT NULL,
    partial_score REAL NOT NULL,
    tests_passed INTEGER,
    tests_total INTEGER,
    forbidden_change_count INTEGER NOT NULL DEFAULT 0,
    grading_details TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (functional_score BETWEEN 0 AND 1),
    CHECK (regression_score BETWEEN 0 AND 1),
    CHECK (constraints_score BETWEEN 0 AND 1),
    CHECK (partial_score BETWEEN 0 AND 1)
);

CREATE TABLE IF NOT EXISTS test_results (
    attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id) ON DELETE CASCADE,
    test_case_id TEXT NOT NULL,
    test_group TEXT NOT NULL,
    passed INTEGER NOT NULL,
    weight REAL NOT NULL DEFAULT 1,
    duration_ms INTEGER,
    failure_signature TEXT,
    details TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (attempt_id, test_case_id)
);

CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    event_time TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    UNIQUE (attempt_id, seq)
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id TEXT PRIMARY KEY,
    config_id TEXT NOT NULL REFERENCES model_configs(config_id),
    metric TEXT NOT NULL,
    severity TEXT NOT NULL,
    baseline_window TEXT NOT NULL,
    current_window TEXT NOT NULL,
    baseline_value REAL,
    current_value REAL,
    absolute_delta REAL,
    relative_delta REAL,
    ci_low REAL,
    ci_high REAL,
    decision_rule TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS suite_members (
    suite_id TEXT NOT NULL,
    task_key TEXT NOT NULL,
    task_version TEXT NOT NULL,
    suite_class TEXT NOT NULL,
    PRIMARY KEY (suite_id, task_key, task_version)
);

CREATE TABLE IF NOT EXISTS baselines (
    baseline_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    track TEXT NOT NULL,
    model TEXT NOT NULL,
    effort TEXT NOT NULL,
    suite_id TEXT NOT NULL,
    suite_version TEXT NOT NULL,
    suite_hash TEXT NOT NULL,
    client_mode TEXT NOT NULL DEFAULT 'latest',
    auth_surface TEXT NOT NULL DEFAULT 'chatgpt',
    grading_protocol TEXT NOT NULL DEFAULT 'hidden_final_state',
    locked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    locked_at TEXT,
    environment_constraints TEXT NOT NULL DEFAULT '{}',
    harness_commit TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS baseline_runs (
    baseline_id TEXT NOT NULL REFERENCES baselines(baseline_id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL REFERENCES eval_runs(run_id) ON DELETE RESTRICT,
    PRIMARY KEY (baseline_id, run_id)
);

CREATE TABLE IF NOT EXISTS analyses (
    analysis_id TEXT PRIMARY KEY,
    baseline_id TEXT,
    current_run_id TEXT,
    track TEXT,
    model TEXT,
    effort TEXT,
    method TEXT,
    bootstrap_seed INTEGER,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (attempt_id, kind)
);

CREATE TABLE IF NOT EXISTS environment_manifests (
    run_id TEXT PRIMARY KEY REFERENCES eval_runs(run_id) ON DELETE CASCADE,
    manifest TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now')),
    name TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_attempts_run ON attempts(run_id);
CREATE INDEX IF NOT EXISTS idx_attempts_task ON attempts(task_version_id);
CREATE INDEX IF NOT EXISTS idx_attempts_completed ON attempts(completed_at);
CREATE INDEX IF NOT EXISTS idx_attempts_quality ON attempts(quality_status);
CREATE INDEX IF NOT EXISTS idx_events_attempt ON events(attempt_id, seq);
CREATE INDEX IF NOT EXISTS idx_scores_pass ON scores(strict_pass);
CREATE INDEX IF NOT EXISTS idx_baselines_name ON baselines(name);

CREATE TABLE IF NOT EXISTS judgments (
    judgment_id TEXT PRIMARY KEY,
    attempt_id TEXT REFERENCES attempts(attempt_id) ON DELETE CASCADE,
    subject TEXT NOT NULL DEFAULT 'attempt',
    task_key TEXT NOT NULL,
    rubric_version TEXT NOT NULL,
    judge_model TEXT NOT NULL,
    judge_effort TEXT NOT NULL,
    repeat_index INTEGER NOT NULL DEFAULT 0,
    prompt_sha256 TEXT NOT NULL,
    overall REAL,
    scores TEXT,
    rationale TEXT,
    unsupported_claims TEXT,
    summary TEXT,
    status TEXT NOT NULL,
    error TEXT,
    equivalent_cost_usd REAL,
    served_model TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS behaviors (
    attempt_id TEXT PRIMARY KEY REFERENCES attempts(attempt_id) ON DELETE CASCADE,
    behavior_version TEXT NOT NULL,
    metrics TEXT NOT NULL,
    flags TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
