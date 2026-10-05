from pathlib import Path

from runner.tasks import discover_tasks, validate_task


def test_ten_starter_tasks_exist() -> None:
    tasks = discover_tasks(Path("tasks"))
    # Mined tasks are materialized locally from configs/mined.yaml and gitignored.
    ids = {task.id for task in tasks if not task.id.startswith("MINE-")}
    assert ids == {
        "BUG-PY-01",
        "BUG-TS-01",
        "REF-PY-01",
        "FEAT-TS-01",
        "DATA-PY-01",
        "SPEC-TS-01",
        "BUILD-01",
        "STATE-GO-01",
        "NAV-RUST-01",
        "LONG-01",
    }


def test_manifests_validate_without_frozen_hashes() -> None:
    for task in discover_tasks(Path("tasks")):
        errors = validate_task(task, check_hashes=False)
        assert errors == [], errors
