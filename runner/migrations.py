"""SQLite/Postgres-safe schema migrations.

SQLite remains the default system of record. PostgreSQL is optional and is
only maintained when the extra cost is small.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

SCHEMA_PATH = Path(__file__).with_name("sqlite_schema.sql")
MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now')),
    name TEXT NOT NULL
)
"""


def _column_names(conn: Connection, table: str) -> set[str]:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {str(row[1]) for row in rows}


def _table_exists(conn: Connection, table: str) -> bool:
    row = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:n"),
        {"n": table},
    ).fetchone()
    return row is not None


def _applied(conn: Connection) -> set[int]:
    conn.execute(text(MIGRATIONS_TABLE))
    rows = conn.execute(text("SELECT version FROM schema_migrations")).fetchall()
    return {int(row[0]) for row in rows}


def _mark(conn: Connection, version: int, name: str) -> None:
    conn.execute(
        text("INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (:v, :n)"),
        {"v": version, "n": name},
    )


def apply_sqlite_migrations(engine: Engine) -> None:
    with engine.begin() as conn:
        sql = SCHEMA_PATH.read_text(encoding="utf-8")
        deferred_indexes: list[str] = []
        for statement in _split_sql(sql):
            if statement.lstrip().upper().startswith("CREATE INDEX"):
                deferred_indexes.append(statement)
                continue
            conn.execute(text(statement))
        applied = _applied(conn)
        if 2 not in applied:
            _migrate_002(conn)
            _mark(conn, 2, "codex_sol_identity")
        if 3 not in applied:
            _migrate_003(conn)
            _mark(conn, 3, "baselines_artifacts_manifests")
        if 4 not in applied:
            _migrate_004(conn)
            _mark(conn, 4, "dual_model_pairing_scientific")
        for statement in deferred_indexes:
            conn.execute(text(statement))


def _migrate_002(conn: Connection) -> None:
    """Allow max effort and persist requested/verified identity on attempts."""
    ddl = conn.execute(
        text("SELECT sql FROM sqlite_master WHERE type='table' AND name='model_configs'")
    ).scalar()
    needs_rebuild = bool(ddl) and "'max'" not in str(ddl)
    if needs_rebuild:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        conn.execute(text("ALTER TABLE model_configs RENAME TO model_configs_legacy"))
        conn.execute(
            text(
                """
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
                    CHECK (reasoning_effort IN ('low', 'medium', 'high', 'xhigh', 'max', 'ultra'))
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO model_configs (
                    config_id, provider, request_model, reasoning_effort,
                    temperature, top_p, max_output_tokens, system_prompt_sha256,
                    tools_sha256, config, config_sha256, created_at
                )
                SELECT
                    config_id, provider, request_model, reasoning_effort,
                    temperature, top_p, max_output_tokens, system_prompt_sha256,
                    tools_sha256, config, config_sha256, created_at
                FROM model_configs_legacy
                """
            )
        )
        conn.execute(text("DROP TABLE model_configs_legacy"))
        conn.execute(text("PRAGMA foreign_keys=ON"))

    if _table_exists(conn, "attempts"):
        existing = _column_names(conn, "attempts")
        additions = {
            "requested_effort": "TEXT",
            "verified_effort": "TEXT",
            "verified_model": "TEXT",
            "quality_status": "TEXT",
            "track": "TEXT",
            "client_mode": "TEXT",
            "auth_surface": "TEXT",
            "thread_id": "TEXT",
            "codex_version": "TEXT",
            "codex_sha256": "TEXT",
            "schedule_order": "INTEGER",
        }
        for name, decl in additions.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE attempts ADD COLUMN {name} {decl}"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_attempts_quality ON attempts(quality_status)"))

    if _table_exists(conn, "eval_runs"):
        existing = _column_names(conn, "eval_runs")
        for name, decl in {
            "track": "TEXT",
            "client_mode": "TEXT",
            "schedule_seed": "INTEGER",
            "suite_hash": "TEXT",
            "requested_model": "TEXT",
            "verified_model": "TEXT",
            "requested_effort": "TEXT",
            "verified_effort": "TEXT",
            "auth_surface": "TEXT",
        }.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE eval_runs ADD COLUMN {name} {decl}"))


def _migrate_003(conn: Connection) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS suite_members (
                suite_id TEXT NOT NULL,
                task_key TEXT NOT NULL,
                task_version TEXT NOT NULL,
                suite_class TEXT NOT NULL,
                PRIMARY KEY (suite_id, task_key, task_version)
            )
            """
        )
    )
    conn.execute(
        text(
            """
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
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS baseline_runs (
                baseline_id TEXT NOT NULL REFERENCES baselines(baseline_id) ON DELETE RESTRICT,
                run_id TEXT NOT NULL REFERENCES eval_runs(run_id) ON DELETE RESTRICT,
                PRIMARY KEY (baseline_id, run_id)
            )
            """
        )
    )
    conn.execute(
        text(
            """
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
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id TEXT PRIMARY KEY,
                attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE (attempt_id, kind)
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS environment_manifests (
                run_id TEXT PRIMARY KEY REFERENCES eval_runs(run_id) ON DELETE CASCADE,
                manifest TEXT NOT NULL,
                manifest_sha256 TEXT NOT NULL
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS alert_policies (
                policy_id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
    )


def _migrate_004(conn: Connection) -> None:
    """Pair keys, scientific_data flags, and protocol versions for dual-model series."""
    if _table_exists(conn, "eval_runs"):
        existing = _column_names(conn, "eval_runs")
        for name, decl in {
            "source": "TEXT",
            "scientific_data": "INTEGER",
            "protocol_version": "TEXT",
            "tool_protocol_version": "TEXT",
            "bootstrap_seed": "INTEGER",
            "fixture_seed": "INTEGER",
            "grader_seed": "INTEGER",
        }.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE eval_runs ADD COLUMN {name} {decl}"))

    if _table_exists(conn, "attempts"):
        existing = _column_names(conn, "attempts")
        for name, decl in {
            "pair_key": "TEXT",
            "protocol_version": "TEXT",
            "tool_protocol_version": "TEXT",
            "scientific_data": "INTEGER",
            "schedule_seed": "INTEGER",
            "bootstrap_seed": "INTEGER",
            "fixture_seed": "INTEGER",
            "grader_seed": "INTEGER",
            "source": "TEXT",
        }.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE attempts ADD COLUMN {name} {decl}"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_attempts_pair_key ON attempts(pair_key)"))

    if _table_exists(conn, "baselines"):
        existing = _column_names(conn, "baselines")
        for name, decl in {
            "scientific_data": "INTEGER",
            "source": "TEXT",
            "series_key": "TEXT",
            "qualified": "INTEGER",
            "provider": "TEXT",
            "protocol_version": "TEXT",
            "tool_protocol_version": "TEXT",
        }.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE baselines ADD COLUMN {name} {decl}"))


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
