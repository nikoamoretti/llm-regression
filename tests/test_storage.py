import sqlite3

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from runner.storage import Store


def test_legacy_db_gains_quality_status_and_max(tmp_path) -> None:
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE task_suites (
            suite_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            version TEXT NOT NULL,
            git_sha TEXT NOT NULL,
            manifest_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (name, version),
            UNIQUE (manifest_sha256)
        );
        CREATE TABLE tasks (
            task_version_id TEXT PRIMARY KEY,
            suite_id TEXT NOT NULL,
            task_key TEXT NOT NULL,
            task_version TEXT NOT NULL,
            category TEXT NOT NULL,
            prompt_sha256 TEXT NOT NULL,
            fixture_sha256 TEXT NOT NULL,
            grader_sha256 TEXT NOT NULL,
            container_image_digest TEXT NOT NULL,
            manifest TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE model_configs (
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
            CHECK (reasoning_effort IN ('low', 'medium', 'high', 'xhigh'))
        );
        CREATE TABLE eval_runs (
            run_id TEXT PRIMARY KEY,
            suite_id TEXT NOT NULL,
            config_id TEXT NOT NULL,
            trigger TEXT NOT NULL,
            status TEXT NOT NULL,
            runner_git_sha TEXT NOT NULL,
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at TEXT
        );
        CREATE TABLE attempts (
            attempt_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            task_version_id TEXT NOT NULL,
            trial_index INTEGER NOT NULL,
            logical_attempt_key TEXT NOT NULL,
            requested_model TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (run_id, task_version_id, trial_index),
            UNIQUE (logical_attempt_key)
        );
        CREATE TABLE scores (
            attempt_id TEXT PRIMARY KEY,
            strict_pass INTEGER NOT NULL,
            functional_score REAL NOT NULL,
            regression_score REAL NOT NULL,
            constraints_score REAL NOT NULL,
            partial_score REAL NOT NULL
        );
        CREATE TABLE events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            attempt_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            event_time TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL
        );
        CREATE TABLE alerts (
            alert_id TEXT PRIMARY KEY,
            config_id TEXT NOT NULL,
            metric TEXT NOT NULL,
            severity TEXT NOT NULL,
            baseline_window TEXT NOT NULL,
            current_window TEXT NOT NULL,
            decision_rule TEXT NOT NULL
        );
        CREATE TABLE test_results (
            attempt_id TEXT NOT NULL,
            test_case_id TEXT NOT NULL,
            test_group TEXT NOT NULL,
            passed INTEGER NOT NULL,
            PRIMARY KEY (attempt_id, test_case_id)
        );
        """
    )
    conn.commit()
    conn.close()
    store = Store(f"sqlite:///{path}")
    with store.engine.begin() as db:
        cols = {row[1] for row in db.execute(text("PRAGMA table_info(attempts)"))}
        ddl = db.execute(text("SELECT sql FROM sqlite_master WHERE name='model_configs'")).scalar()
        versions = [row[0] for row in db.execute(text("SELECT version FROM schema_migrations"))]
    assert "quality_status" in cols
    assert "verified_effort" in cols
    assert "'max'" in str(ddl)
    assert 2 in versions and 3 in versions and 4 in versions
    assert "pair_key" in cols


def test_schema_is_idempotent(tmp_path) -> None:
    url = f"sqlite:///{tmp_path / 'reg.db'}"
    Store(url)
    Store(url)


def test_max_effort_is_accepted() -> None:
    store = Store("sqlite://")
    cfg = store.upsert_model_config(
        {
            "provider": "codex_cli",
            "request_model": "gpt-5.6-sol",
            "reasoning_effort": "max",
            "config": {"model": "gpt-5.6-sol", "effort": "max"},
            "config_sha256": "cfg-max",
        }
    )
    assert cfg


def test_attempt_unique_constraint() -> None:
    store = Store("sqlite://")
    suite = store.upsert_suite(name="s", version="1", git_sha="abc", manifest_sha256="m1")
    task = store.upsert_task(
        suite,
        {
            "task_key": "T",
            "task_version": "1",
            "category": "bugfix",
            "prompt_sha256": "p",
            "fixture_sha256": "f",
            "grader_sha256": "g",
            "container_image_digest": "local",
        },
    )
    cfg = store.upsert_model_config(
        {
            "provider": "codex_cli",
            "request_model": "gpt-5.6-sol",
            "reasoning_effort": "max",
            "config": {"model": "gpt-5.6-sol"},
            "config_sha256": "cfg1",
        }
    )
    run = store.create_run(
        {"suite_id": suite, "config_id": cfg, "trigger": "t", "status": "running", "runner_git_sha": "x"}
    )
    store.create_attempt(
        {
            "run_id": run,
            "task_version_id": task,
            "trial_index": 0,
            "logical_attempt_key": "k1",
            "requested_model": "gpt-5.6-sol",
        }
    )
    with pytest.raises(IntegrityError):
        store.create_attempt(
            {
                "run_id": run,
                "task_version_id": task,
                "trial_index": 0,
                "logical_attempt_key": "k2",
                "requested_model": "gpt-5.6-sol",
            }
        )


def test_file_backed_sqlite_creates_missing_parent_dir(tmp_path) -> None:
    path = tmp_path / "fresh" / "nested" / "regression.db"
    store = Store(f"sqlite:///{path}")
    with store.engine.begin() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1
    assert path.exists()
