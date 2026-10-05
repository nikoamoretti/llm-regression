"""Immutable baseline entities.

A locked baseline cannot be edited. Comparisons against a different track,
model, effort, suite hash, client mode, or grading protocol are rejected
unless the caller forces a visibly non-canonical comparison.

Gold, fake, synthetic, and other non-scientific runs cannot become baselines.
"""

from __future__ import annotations

from typing import Any

from runner import NON_SCIENTIFIC_SOURCES
from runner.identity import SeriesIdentity, identity_from_mapping, require_compatible
from runner.scientific import reject_non_scientific
from runner.storage import Store, new_id, utcnow


def _run_is_scientific(run: dict[str, Any]) -> None:
    source = run.get("source")
    scientific = run.get("scientific_data")
    if scientific is None and source is None:
        notes = str(run.get("notes") or "")
        for token in NON_SCIENTIFIC_SOURCES:
            if token in notes.split():
                source = token
                break
    if scientific is not None:
        scientific = bool(int(scientific))
    reject_non_scientific(source, scientific_data=scientific)


def qualify_runs(store: Store, run_ids: list[str]) -> dict[str, Any]:
    if not run_ids:
        raise ValueError("qualify requires at least one run id")
    problems: list[str] = []
    runs: list[dict[str, Any]] = []
    for run_id in run_ids:
        run = store.get_run(run_id)
        if run is None:
            problems.append(f"unknown run {run_id}")
            continue
        runs.append(run)
        try:
            _run_is_scientific(run)
        except ValueError as exc:
            problems.append(str(exc))
    if not runs:
        return {"ok": False, "qualified": False, "problems": problems, "runs": []}
    keys = {
        (
            run.get("track"),
            run.get("requested_model"),
            run.get("requested_effort"),
            run.get("suite_hash"),
            run.get("protocol_version"),
            run.get("tool_protocol_version"),
        )
        for run in runs
    }
    if len(keys) != 1:
        problems.append(
            "runs do not share one series (model × effort × track × suite × protocol): "
            + ", ".join(str(item) for item in sorted(keys, key=str))
        )
    statuses = {run.get("status") for run in runs}
    if any(status not in {"completed", "completed_with_errors"} for status in statuses):
        problems.append(f"incomplete runs: {sorted(statuses)}")
    first = runs[0]
    return {
        "ok": not problems,
        "qualified": not problems,
        "problems": problems,
        "series": {
            "track": first.get("track"),
            "model": first.get("requested_model"),
            "effort": first.get("requested_effort"),
            "suite_hash": first.get("suite_hash"),
            "protocol_version": first.get("protocol_version"),
            "tool_protocol_version": first.get("tool_protocol_version"),
        },
        "run_ids": run_ids,
    }


def create_baseline(
    store: Store,
    *,
    name: str,
    run_ids: list[str],
    track: str,
    model: str,
    effort: str,
    suite_id: str,
    suite_version: str,
    suite_hash: str,
    client_mode: str = "latest",
    auth_surface: str = "chatgpt",
    grading_protocol: str = "hidden_final_state",
    environment_constraints: dict[str, Any] | None = None,
    harness_commit: str = "unknown",
    notes: str = "",
    provider: str = "",
    protocol_version: str = "1.0.0",
    tool_protocol_version: str = "2.0.0",
    require_qualified: bool = True,
) -> str:
    if not run_ids:
        raise ValueError("baseline create requires at least one run id")
    qualification = qualify_runs(store, run_ids)
    if require_qualified and not qualification["ok"]:
        raise ValueError("baseline qualification failed: " + "; ".join(qualification["problems"]))
    series = qualification.get("series") or {}
    if series.get("track") and series["track"] != track:
        raise ValueError(f"run track {series['track']} does not match requested {track}")
    if series.get("model") and series["model"] != model:
        raise ValueError(f"run model {series['model']} does not match requested {model}")
    if series.get("effort") and series["effort"] != effort:
        raise ValueError(f"run effort {series['effort']} does not match requested {effort}")
    identity = SeriesIdentity(
        track=track,
        model=model,
        effort=effort,
        client_mode=client_mode,
        suite_id=suite_id,
        suite_version=suite_version,
        suite_hash=suite_hash,
        grading_protocol=grading_protocol,
        auth_surface=auth_surface,
        provider=provider,
        protocol_version=protocol_version,
        tool_protocol_version=tool_protocol_version,
        scientific_data=True,
    )
    payload = {
        "baseline_id": new_id(),
        "name": name,
        "track": track,
        "model": model,
        "effort": effort,
        "suite_id": suite_id,
        "suite_version": suite_version,
        "suite_hash": suite_hash,
        "client_mode": client_mode,
        "auth_surface": auth_surface,
        "grading_protocol": grading_protocol,
        "locked": 0,
        "created_at": utcnow(),
        "locked_at": None,
        "environment_constraints": environment_constraints or {},
        "harness_commit": harness_commit,
        "notes": notes,
        "run_ids": run_ids,
        "scientific_data": True,
        "source": "model",
        "series_key": identity.series_key(),
        "qualified": 1,
        "provider": provider,
        "protocol_version": protocol_version,
        "tool_protocol_version": tool_protocol_version,
    }
    return store.create_baseline(payload)


def show_baseline(store: Store, name_or_id: str) -> dict[str, Any]:
    row = store.get_baseline(name_or_id)
    if row is None:
        raise KeyError(f"unknown baseline: {name_or_id}")
    return row


def list_baselines(store: Store) -> list[dict[str, Any]]:
    return store.list_baselines()


def lock_baseline(store: Store, name_or_id: str) -> dict[str, Any]:
    row = store.get_baseline(name_or_id)
    if row is None:
        raise KeyError(f"unknown baseline: {name_or_id}")
    if row.get("locked"):
        raise ValueError(f"baseline {row['name']} is already locked")
    store.lock_baseline(row["baseline_id"])
    locked = store.get_baseline(row["baseline_id"])
    assert locked is not None
    return locked


def assert_baseline_mutable(store: Store, name_or_id: str) -> None:
    row = store.get_baseline(name_or_id)
    if row is None:
        raise KeyError(f"unknown baseline: {name_or_id}")
    if row.get("locked"):
        raise ValueError(f"locked baseline {row['name']} cannot be edited")


def baseline_identity(row: dict[str, Any]) -> SeriesIdentity:
    return identity_from_mapping(
        {
            "track": row["track"],
            "model": row["model"],
            "effort": row["effort"],
            "client_mode": row.get("client_mode", "latest"),
            "suite_id": row["suite_id"],
            "suite_version": row.get("suite_version", ""),
            "suite_hash": row.get("suite_hash", ""),
            "grading_protocol": row.get("grading_protocol", "hidden_final_state"),
            "auth_surface": row.get("auth_surface", "chatgpt"),
            "provider": row.get("provider") or "",
            "protocol_version": row.get("protocol_version") or "1.0.0",
            "tool_protocol_version": row.get("tool_protocol_version") or "2.0.0",
            "scientific_data": row.get("scientific_data"),
        }
    )


def compare_identities(
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    force: bool = False,
) -> dict[str, Any]:
    return require_compatible(baseline_identity(baseline), identity_from_mapping(current), force=force)
