"""Persistence for suites, configs, attempts, scores, events, and alerts."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, make_url, text
from sqlalchemy.engine import Engine

from runner.hash_tree import hash_bytes
from runner.migrations import apply_sqlite_migrations

SCHEMA_PATH = Path(__file__).with_name("sqlite_schema.sql")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


class Store:
    def __init__(self, database_url: str = "sqlite:///./artifacts/regression.db") -> None:
        self.database_url = database_url
        # Parallel attempts (evaluate --workers) share one SQLite file; writers wait instead of failing.
        connect_args = {"check_same_thread": False, "timeout": 120} if database_url.startswith("sqlite") else {}
        if database_url.startswith("sqlite"):
            db_path = make_url(database_url).database
            if db_path and db_path != ":memory:":
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.engine: Engine = create_engine(database_url, future=True, connect_args=connect_args)
        if database_url.startswith("sqlite"):
            file_backed = database_url.startswith("sqlite:///") and ":memory:" not in database_url

            @event.listens_for(self.engine, "connect")
            def _fk(dbapi_connection, _connection_record):  # type: ignore[no-untyped-def]
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                if file_backed:
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.execute("PRAGMA synchronous=FULL")
                cursor.close()

        self.ensure_schema()

    def ensure_schema(self) -> None:
        if self.database_url.startswith("sqlite"):
            apply_sqlite_migrations(self.engine)
            return
        sql = SCHEMA_PATH.read_text(encoding="utf-8")
        with self.engine.begin() as conn:
            for statement in _split_sql(sql):
                conn.execute(text(statement))

    def upsert_suite(
        self,
        *,
        name: str,
        version: str,
        git_sha: str,
        manifest_sha256: str,
    ) -> str:
        with self.engine.begin() as conn:
            row = conn.execute(
                text("SELECT suite_id FROM task_suites WHERE name = :n AND version = :v"),
                {"n": name, "v": version},
            ).fetchone()
            if row:
                return str(row[0])
            suite_id = new_id()
            conn.execute(
                text(
                    """
                    INSERT INTO task_suites (suite_id, name, version, git_sha, manifest_sha256)
                    VALUES (:id, :n, :v, :sha, :man)
                    """
                ),
                {"id": suite_id, "n": name, "v": version, "sha": git_sha, "man": manifest_sha256},
            )
            return suite_id

    def upsert_task(self, suite_id: str, record: dict[str, Any]) -> str:
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT task_version_id FROM tasks
                    WHERE suite_id = :s AND task_key = :k AND task_version = :v
                    """
                ),
                {"s": suite_id, "k": record["task_key"], "v": record["task_version"]},
            ).fetchone()
            if row:
                return str(row[0])
            task_id = new_id()
            conn.execute(
                text(
                    """
                    INSERT INTO tasks (
                        task_version_id, suite_id, task_key, task_version, category,
                        language, difficulty, prompt_sha256, fixture_sha256, grader_sha256,
                        container_image_digest, context_class, manifest, active
                    ) VALUES (
                        :id, :suite, :key, :ver, :cat, :lang, :diff, :p, :f, :g,
                        :img, :ctx, :man, 1
                    )
                    """
                ),
                {
                    "id": task_id,
                    "suite": suite_id,
                    "key": record["task_key"],
                    "ver": record["task_version"],
                    "cat": record["category"],
                    "lang": record.get("language"),
                    "diff": record.get("difficulty"),
                    "p": record["prompt_sha256"],
                    "f": record["fixture_sha256"],
                    "g": record["grader_sha256"],
                    "img": record["container_image_digest"],
                    "ctx": record.get("context_class"),
                    "man": _json(record.get("manifest", {})),
                },
            )
            return task_id

    def upsert_model_config(self, record: dict[str, Any]) -> str:
        with self.engine.begin() as conn:
            row = conn.execute(
                text("SELECT config_id FROM model_configs WHERE config_sha256 = :h"),
                {"h": record["config_sha256"]},
            ).fetchone()
            if row:
                return str(row[0])
            config_id = new_id()
            conn.execute(
                text(
                    """
                    INSERT INTO model_configs (
                        config_id, provider, request_model, reasoning_effort,
                        temperature, top_p, max_output_tokens,
                        system_prompt_sha256, tools_sha256, config, config_sha256
                    ) VALUES (
                        :id, :prov, :model, :effort, :temp, :top, :max,
                        :sys, :tools, :cfg, :sha
                    )
                    """
                ),
                {
                    "id": config_id,
                    "prov": record["provider"],
                    "model": record["request_model"],
                    "effort": record["reasoning_effort"],
                    "temp": record.get("temperature"),
                    "top": record.get("top_p"),
                    "max": record.get("max_output_tokens"),
                    "sys": record.get("system_prompt_sha256"),
                    "tools": record.get("tools_sha256"),
                    "cfg": _json(record["config"]),
                    "sha": record["config_sha256"],
                },
            )
            return config_id

    def create_run(self, record: dict[str, Any]) -> str:
        run_id = record.get("run_id") or new_id()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO eval_runs (
                        run_id, suite_id, config_id, baseline_run_id, trigger, status,
                        runner_git_sha, runner_image_digest, environment, notes,
                        track, client_mode, schedule_seed, suite_hash,
                        requested_model, verified_model, requested_effort, verified_effort,
                        auth_surface, source, scientific_data, protocol_version,
                        tool_protocol_version, bootstrap_seed, fixture_seed, grader_seed
                    ) VALUES (
                        :id, :suite, :cfg, :base, :trig, :status, :rsha, :img, :env, :notes,
                        :track, :client_mode, :schedule_seed, :suite_hash,
                        :requested_model, :verified_model, :requested_effort, :verified_effort,
                        :auth_surface, :source, :scientific_data, :protocol_version,
                        :tool_protocol_version, :bootstrap_seed, :fixture_seed, :grader_seed
                    )
                    """
                ),
                {
                    "id": run_id,
                    "suite": record["suite_id"],
                    "cfg": record["config_id"],
                    "base": record.get("baseline_run_id"),
                    "trig": record.get("trigger", "manual"),
                    "status": record.get("status", "running"),
                    "rsha": record.get("runner_git_sha", "unknown"),
                    "img": record.get("runner_image_digest"),
                    "env": _json(record.get("environment", {})),
                    "notes": record.get("notes"),
                    "track": record.get("track"),
                    "client_mode": record.get("client_mode"),
                    "schedule_seed": record.get("schedule_seed"),
                    "suite_hash": record.get("suite_hash"),
                    "requested_model": record.get("requested_model"),
                    "verified_model": record.get("verified_model"),
                    "requested_effort": record.get("requested_effort"),
                    "verified_effort": record.get("verified_effort"),
                    "auth_surface": record.get("auth_surface"),
                    "source": record.get("source"),
                    "scientific_data": (
                        None
                        if record.get("scientific_data") is None
                        else int(bool(record.get("scientific_data")))
                    ),
                    "protocol_version": record.get("protocol_version"),
                    "tool_protocol_version": record.get("tool_protocol_version"),
                    "bootstrap_seed": record.get("bootstrap_seed"),
                    "fixture_seed": record.get("fixture_seed"),
                    "grader_seed": record.get("grader_seed"),
                },
            )
        return run_id

    def complete_run(self, run_id: str, status: str = "completed") -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE eval_runs
                    SET status = :s, completed_at = :t
                    WHERE run_id = :id
                    """
                ),
                {"s": status, "t": utcnow(), "id": run_id},
            )

    def create_attempt(self, record: dict[str, Any]) -> str:
        attempt_id = record.get("attempt_id") or new_id()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO attempts (
                        attempt_id, run_id, task_version_id, trial_index, logical_attempt_key,
                        requested_model, resolved_model, api_response_id, status, retry_count,
                        started_at, completed_at, api_latency_ms, wall_time_ms, tool_time_ms,
                        grader_time_ms, input_tokens, cached_input_tokens, output_tokens,
                        reasoning_tokens, total_tokens, cost_usd_ticks, x_zero_data_retention,
                        response_sha256, workspace_diff_sha256, response_artifact_uri,
                        trace_artifact_uri, diff_artifact_uri, exit_code, error_code, error,
                        requested_effort, verified_effort, verified_model, quality_status,
                        track, client_mode, auth_surface, thread_id, codex_version,
                        codex_sha256, schedule_order, pair_key, protocol_version,
                        tool_protocol_version, scientific_data, schedule_seed,
                        bootstrap_seed, fixture_seed, grader_seed, source
                    ) VALUES (
                        :attempt_id, :run_id, :task_version_id, :trial_index, :logical_attempt_key,
                        :requested_model, :resolved_model, :api_response_id, :status, :retry_count,
                        :started_at, :completed_at, :api_latency_ms, :wall_time_ms, :tool_time_ms,
                        :grader_time_ms, :input_tokens, :cached_input_tokens, :output_tokens,
                        :reasoning_tokens, :total_tokens, :cost_usd_ticks, :x_zero_data_retention,
                        :response_sha256, :workspace_diff_sha256, :response_artifact_uri,
                        :trace_artifact_uri, :diff_artifact_uri, :exit_code, :error_code, :error,
                        :requested_effort, :verified_effort, :verified_model, :quality_status,
                        :track, :client_mode, :auth_surface, :thread_id, :codex_version,
                        :codex_sha256, :schedule_order, :pair_key, :protocol_version,
                        :tool_protocol_version, :scientific_data, :schedule_seed,
                        :bootstrap_seed, :fixture_seed, :grader_seed, :source
                    )
                    """
                ),
                {
                    "attempt_id": attempt_id,
                    "run_id": record["run_id"],
                    "task_version_id": record["task_version_id"],
                    "trial_index": record["trial_index"],
                    "logical_attempt_key": record["logical_attempt_key"],
                    "requested_model": record["requested_model"],
                    "resolved_model": record.get("resolved_model"),
                    "api_response_id": record.get("api_response_id"),
                    "status": record.get("status", "created"),
                    "retry_count": record.get("retry_count", 0),
                    "started_at": record.get("started_at", utcnow()),
                    "completed_at": record.get("completed_at"),
                    "api_latency_ms": record.get("api_latency_ms"),
                    "wall_time_ms": record.get("wall_time_ms"),
                    "tool_time_ms": record.get("tool_time_ms"),
                    "grader_time_ms": record.get("grader_time_ms"),
                    "input_tokens": record.get("input_tokens"),
                    "cached_input_tokens": record.get("cached_input_tokens"),
                    "output_tokens": record.get("output_tokens"),
                    "reasoning_tokens": record.get("reasoning_tokens"),
                    "total_tokens": record.get("total_tokens"),
                    "cost_usd_ticks": record.get("cost_usd_ticks"),
                    "x_zero_data_retention": (
                        None
                        if record.get("x_zero_data_retention") is None
                        else int(bool(record.get("x_zero_data_retention")))
                    ),
                    "response_sha256": record.get("response_sha256"),
                    "workspace_diff_sha256": record.get("workspace_diff_sha256"),
                    "response_artifact_uri": record.get("response_artifact_uri"),
                    "trace_artifact_uri": record.get("trace_artifact_uri"),
                    "diff_artifact_uri": record.get("diff_artifact_uri"),
                    "exit_code": record.get("exit_code"),
                    "error_code": record.get("error_code"),
                    "error": None if record.get("error") is None else _json(record.get("error")),
                    "requested_effort": record.get("requested_effort"),
                    "verified_effort": record.get("verified_effort"),
                    "verified_model": record.get("verified_model"),
                    "quality_status": record.get("quality_status"),
                    "track": record.get("track"),
                    "client_mode": record.get("client_mode"),
                    "auth_surface": record.get("auth_surface"),
                    "thread_id": record.get("thread_id"),
                    "codex_version": record.get("codex_version"),
                    "codex_sha256": record.get("codex_sha256"),
                    "schedule_order": record.get("schedule_order"),
                    "pair_key": record.get("pair_key"),
                    "protocol_version": record.get("protocol_version"),
                    "tool_protocol_version": record.get("tool_protocol_version"),
                    "scientific_data": (
                        None
                        if record.get("scientific_data") is None
                        else int(bool(record.get("scientific_data")))
                    ),
                    "schedule_seed": record.get("schedule_seed"),
                    "bootstrap_seed": record.get("bootstrap_seed"),
                    "fixture_seed": record.get("fixture_seed"),
                    "grader_seed": record.get("grader_seed"),
                    "source": record.get("source"),
                },
            )
        return attempt_id

    def update_attempt(self, attempt_id: str, fields: dict[str, Any]) -> None:
        if not fields:
            return
        assignments = []
        params: dict[str, Any] = {"id": attempt_id}
        for key, value in fields.items():
            if key in {"error", "environment"} and value is not None and not isinstance(value, str):
                value = _json(value)
            if key in {"x_zero_data_retention", "scientific_data"} and value is not None:
                value = int(bool(value))
            assignments.append(f"{key} = :{key}")
            params[key] = value
        sql = f"UPDATE attempts SET {', '.join(assignments)} WHERE attempt_id = :id"
        with self.engine.begin() as conn:
            conn.execute(text(sql), params)

    def add_score(self, record: dict[str, Any]) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO scores (
                        attempt_id, strict_pass, functional_score, regression_score,
                        constraints_score, partial_score, tests_passed, tests_total,
                        forbidden_change_count, grading_details
                    ) VALUES (
                        :attempt_id, :strict_pass, :functional_score, :regression_score,
                        :constraints_score, :partial_score, :tests_passed, :tests_total,
                        :forbidden_change_count, :grading_details
                    )
                    """
                ),
                {
                    "attempt_id": record["attempt_id"],
                    "strict_pass": int(bool(record["strict_pass"])),
                    "functional_score": record["functional_score"],
                    "regression_score": record["regression_score"],
                    "constraints_score": record["constraints_score"],
                    "partial_score": record["partial_score"],
                    "tests_passed": record.get("tests_passed"),
                    "tests_total": record.get("tests_total"),
                    "forbidden_change_count": record.get("forbidden_change_count", 0),
                    "grading_details": _json(record.get("grading_details", {})),
                },
            )

    def add_test_result(self, record: dict[str, Any]) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO test_results (
                        attempt_id, test_case_id, test_group, passed, weight,
                        duration_ms, failure_signature, details
                    ) VALUES (
                        :attempt_id, :test_case_id, :test_group, :passed, :weight,
                        :duration_ms, :failure_signature, :details
                    )
                    """
                ),
                {
                    "attempt_id": record["attempt_id"],
                    "test_case_id": record["test_case_id"],
                    "test_group": record["test_group"],
                    "passed": int(bool(record["passed"])),
                    "weight": record.get("weight", 1),
                    "duration_ms": record.get("duration_ms"),
                    "failure_signature": record.get("failure_signature"),
                    "details": _json(record.get("details", {})),
                },
            )

    def add_event(self, attempt_id: str, seq: int, event_type: str, payload: dict[str, Any]) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO events (attempt_id, seq, event_time, event_type, payload)
                    VALUES (:a, :s, :t, :ty, :p)
                    """
                ),
                {
                    "a": attempt_id,
                    "s": seq,
                    "t": utcnow(),
                    "ty": event_type,
                    "p": _json(payload),
                },
            )

    def add_alert(self, record: dict[str, Any]) -> str:
        alert_id = record.get("alert_id") or new_id()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO alerts (
                        alert_id, config_id, metric, severity, baseline_window,
                        current_window, baseline_value, current_value, absolute_delta,
                        relative_delta, ci_low, ci_high, decision_rule, status
                    ) VALUES (
                        :alert_id, :config_id, :metric, :severity, :baseline_window,
                        :current_window, :baseline_value, :current_value, :absolute_delta,
                        :relative_delta, :ci_low, :ci_high, :decision_rule, :status
                    )
                    """
                ),
                {
                    "alert_id": alert_id,
                    "config_id": record["config_id"],
                    "metric": record["metric"],
                    "severity": record["severity"],
                    "baseline_window": _json(record.get("baseline_window", {})),
                    "current_window": _json(record.get("current_window", {})),
                    "baseline_value": record.get("baseline_value"),
                    "current_value": record.get("current_value"),
                    "absolute_delta": record.get("absolute_delta"),
                    "relative_delta": record.get("relative_delta"),
                    "ci_low": record.get("ci_low"),
                    "ci_high": record.get("ci_high"),
                    "decision_rule": record["decision_rule"],
                    "status": record.get("status", "open"),
                },
            )
        return alert_id

    def fetch_run_scores(self, run_id: str) -> list[dict[str, Any]]:
        sql = """
        SELECT t.task_key, t.task_version, t.category, a.trial_index, a.status,
               a.quality_status, a.track, a.requested_effort, a.verified_effort,
               s.strict_pass, s.partial_score,
               a.cost_usd_ticks, a.wall_time_ms, a.reasoning_tokens, a.input_tokens,
               a.attempt_id, er.requested_model, er.suite_hash
        FROM attempts a
        JOIN tasks t ON t.task_version_id = a.task_version_id
        JOIN eval_runs er ON er.run_id = a.run_id
        LEFT JOIN scores s ON s.attempt_id = a.attempt_id
        WHERE a.run_id = :run
        ORDER BY t.task_key, a.trial_index
        """
        with self.engine.begin() as conn:
            rows = conn.execute(text(sql), {"run": run_id}).mappings().all()
        return [dict(row) for row in rows]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        sql = "SELECT * FROM eval_runs WHERE run_id = :id"
        with self.engine.begin() as conn:
            row = conn.execute(text(sql), {"id": run_id}).mappings().fetchone()
        return dict(row) if row else None

    def create_baseline(self, record: dict[str, Any]) -> str:
        baseline_id = record.get("baseline_id") or new_id()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO baselines (
                        baseline_id, name, track, model, effort, suite_id, suite_version,
                        suite_hash, client_mode, auth_surface, grading_protocol, locked,
                        created_at, environment_constraints, harness_commit, notes,
                        scientific_data, source, series_key, qualified, provider,
                        protocol_version, tool_protocol_version
                    ) VALUES (
                        :baseline_id, :name, :track, :model, :effort, :suite_id, :suite_version,
                        :suite_hash, :client_mode, :auth_surface, :grading_protocol, :locked,
                        :created_at, :environment_constraints, :harness_commit, :notes,
                        :scientific_data, :source, :series_key, :qualified, :provider,
                        :protocol_version, :tool_protocol_version
                    )
                    """
                ),
                {
                    "baseline_id": baseline_id,
                    "name": record["name"],
                    "track": record["track"],
                    "model": record["model"],
                    "effort": record["effort"],
                    "suite_id": record["suite_id"],
                    "suite_version": record["suite_version"],
                    "suite_hash": record["suite_hash"],
                    "client_mode": record.get("client_mode", "latest"),
                    "auth_surface": record.get("auth_surface", "chatgpt"),
                    "grading_protocol": record.get("grading_protocol", "hidden_final_state"),
                    "locked": int(bool(record.get("locked", 0))),
                    "created_at": record.get("created_at", utcnow()),
                    "environment_constraints": _json(record.get("environment_constraints", {})),
                    "harness_commit": record.get("harness_commit"),
                    "notes": record.get("notes"),
                    "scientific_data": (
                        None
                        if record.get("scientific_data") is None
                        else int(bool(record.get("scientific_data")))
                    ),
                    "source": record.get("source"),
                    "series_key": record.get("series_key"),
                    "qualified": (
                        None
                        if record.get("qualified") is None
                        else int(bool(record.get("qualified")))
                    ),
                    "provider": record.get("provider"),
                    "protocol_version": record.get("protocol_version"),
                    "tool_protocol_version": record.get("tool_protocol_version"),
                },
            )
            for run_id in record.get("run_ids") or []:
                conn.execute(
                    text(
                        """
                        INSERT INTO baseline_runs (baseline_id, run_id)
                        VALUES (:b, :r)
                        """
                    ),
                    {"b": baseline_id, "r": run_id},
                )
        return baseline_id

    def get_baseline(self, name_or_id: str) -> dict[str, Any] | None:
        sql = """
        SELECT * FROM baselines
        WHERE baseline_id = :q OR name = :q
        """
        with self.engine.begin() as conn:
            row = conn.execute(text(sql), {"q": name_or_id}).mappings().fetchone()
            if row is None:
                return None
            data = dict(row)
            runs = conn.execute(
                text("SELECT run_id FROM baseline_runs WHERE baseline_id = :id"),
                {"id": data["baseline_id"]},
            ).fetchall()
        data["run_ids"] = [str(item[0]) for item in runs]
        if isinstance(data.get("environment_constraints"), str):
            data["environment_constraints"] = json.loads(data["environment_constraints"])
        return data

    def list_baselines(self) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            rows = conn.execute(text("SELECT * FROM baselines ORDER BY created_at")).mappings().all()
        return [dict(row) for row in rows]

    def lock_baseline(self, baseline_id: str) -> None:
        with self.engine.begin() as conn:
            row = conn.execute(
                text("SELECT locked FROM baselines WHERE baseline_id = :id"),
                {"id": baseline_id},
            ).fetchone()
            if row is None:
                raise KeyError(baseline_id)
            if int(row[0]):
                raise ValueError("baseline is already locked")
            conn.execute(
                text("UPDATE baselines SET locked = 1, locked_at = :t WHERE baseline_id = :id"),
                {"t": utcnow(), "id": baseline_id},
            )

    def update_baseline(self, baseline_id: str, fields: dict[str, Any]) -> None:
        current = self.get_baseline(baseline_id)
        if current is None:
            raise KeyError(baseline_id)
        if current.get("locked"):
            raise ValueError("locked baseline cannot be edited")
        assignments = []
        params: dict[str, Any] = {"id": baseline_id}
        for key, value in fields.items():
            if key in {"environment_constraints"} and not isinstance(value, str):
                value = _json(value)
            assignments.append(f"{key} = :{key}")
            params[key] = value
        if not assignments:
            return
        with self.engine.begin() as conn:
            conn.execute(
                text(f"UPDATE baselines SET {', '.join(assignments)} WHERE baseline_id = :id"),
                params,
            )

    def fetch_baseline_scores(self, baseline_id: str) -> list[dict[str, Any]]:
        sql = """
        SELECT t.task_key, t.task_version, t.category, a.trial_index, a.status,
               a.quality_status, s.strict_pass, s.partial_score, a.run_id, a.attempt_id,
               a.requested_effort, a.track
        FROM baseline_runs br
        JOIN attempts a ON a.run_id = br.run_id
        JOIN tasks t ON t.task_version_id = a.task_version_id
        LEFT JOIN scores s ON s.attempt_id = a.attempt_id
        WHERE br.baseline_id = :id
        ORDER BY t.task_key, a.trial_index
        """
        with self.engine.begin() as conn:
            rows = conn.execute(text(sql), {"id": baseline_id}).mappings().all()
        return [dict(row) for row in rows]

    def add_artifact(self, attempt_id: str, kind: str, path: str, sha256: str) -> str:
        artifact_id = new_id()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO artifacts (artifact_id, attempt_id, kind, path, sha256)
                    VALUES (:id, :a, :k, :p, :h)
                    """
                ),
                {"id": artifact_id, "a": attempt_id, "k": kind, "p": path, "h": sha256},
            )
        return artifact_id

    def list_artifacts(self, attempt_id: str) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            rows = conn.execute(
                text("SELECT * FROM artifacts WHERE attempt_id = :a"),
                {"a": attempt_id},
            ).mappings().all()
        return [dict(row) for row in rows]

    def put_environment_manifest(self, run_id: str, manifest: dict[str, Any], sha256: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT OR REPLACE INTO environment_manifests (run_id, manifest, manifest_sha256)
                    VALUES (:id, :m, :h)
                    """
                ),
                {"id": run_id, "m": _json(manifest), "h": sha256},
            )

    def get_environment_manifest(self, run_id: str) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                text("SELECT manifest, manifest_sha256 FROM environment_manifests WHERE run_id = :id"),
                {"id": run_id},
            ).fetchone()
        if row is None:
            return None
        return {"manifest": json.loads(row[0]), "manifest_sha256": row[1]}

    def add_analysis(self, record: dict[str, Any]) -> str:
        analysis_id = record.get("analysis_id") or new_id()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO analyses (
                        analysis_id, baseline_id, current_run_id, track, model, effort,
                        method, bootstrap_seed, payload
                    ) VALUES (
                        :analysis_id, :baseline_id, :current_run_id, :track, :model, :effort,
                        :method, :bootstrap_seed, :payload
                    )
                    """
                ),
                {
                    "analysis_id": analysis_id,
                    "baseline_id": record.get("baseline_id"),
                    "current_run_id": record.get("current_run_id"),
                    "track": record.get("track"),
                    "model": record.get("model"),
                    "effort": record.get("effort"),
                    "method": record.get("method"),
                    "bootstrap_seed": record.get("bootstrap_seed"),
                    "payload": _json(record.get("payload", {})),
                },
            )
        return analysis_id

    def list_analyses(self) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            rows = conn.execute(
                text("SELECT * FROM analyses ORDER BY created_at DESC")
            ).mappings().all()
        results = []
        for row in rows:
            data = dict(row)
            if isinstance(data.get("payload"), str):
                data["payload"] = json.loads(data["payload"])
            results.append(data)
        return results

    def latest_analysis_for_effort(self, effort: str) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT * FROM analyses
                    WHERE effort = :e
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"e": effort},
            ).mappings().fetchone()
        if row is None:
            return None
        data = dict(row)
        if isinstance(data.get("payload"), str):
            data["payload"] = json.loads(data["payload"])
        return data

    def verify_attempt_artifacts(self, attempt_id: str) -> list[dict[str, Any]]:
        """Re-hash stored artifacts and fail if any file was tampered with."""
        rows = self.list_artifacts(attempt_id)
        report = []
        for row in rows:
            path = Path(row["path"])
            if not path.exists():
                raise ValueError(f"artifact missing: {path}")
            actual = hash_bytes(path.read_bytes()) if path.is_file() else ""
            if actual != row["sha256"]:
                raise ValueError(
                    f"artifact hash mismatch for {row['kind']}: "
                    f"expected {row['sha256']}, got {actual}"
                )
            report.append({"kind": row["kind"], "sha256": actual, "ok": True})
        return report

    def fetch_attempt(self, attempt_id: str) -> dict[str, Any] | None:
        sql = """
        SELECT a.*, t.task_key, t.task_version, t.category, t.manifest AS task_manifest,
               s.strict_pass, s.partial_score, s.grading_details, er.environment
        FROM attempts a
        JOIN tasks t ON t.task_version_id = a.task_version_id
        JOIN eval_runs er ON er.run_id = a.run_id
        LEFT JOIN scores s ON s.attempt_id = a.attempt_id
        WHERE a.attempt_id = :id
        """
        with self.engine.begin() as conn:
            row = conn.execute(text(sql), {"id": attempt_id}).mappings().fetchone()
        return dict(row) if row else None

    def daily_spend_usd(self, day: str | None = None) -> float:
        day = day or datetime.now(timezone.utc).date().isoformat()
        sql = """
        SELECT COALESCE(SUM(cost_usd_ticks), 0) AS ticks
        FROM attempts
        WHERE started_at LIKE :day
        """
        with self.engine.begin() as conn:
            ticks = conn.execute(text(sql), {"day": f"{day}%"}).scalar_one()
        return float(ticks or 0) / 10_000_000_000


def _split_sql(sql: str) -> list[str]:
    statements = []
    buf: list[str] = []
    for line in sql.splitlines():
        if line.strip().startswith("--"):
            continue
        buf.append(line)
        if line.strip().endswith(";"):
            statement = "\n".join(buf).strip().rstrip(";")
            if statement:
                statements.append(statement)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements
