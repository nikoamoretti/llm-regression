import shutil
from pathlib import Path

import pytest

from runner.tasks import discover_tasks, require_graders, select_tasks, validate_task

FROZEN = {
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


def test_ten_starter_tasks_and_two_demo_tasks_exist() -> None:
    tasks = discover_tasks(Path("tasks"))
    # Mined tasks are materialized locally from configs/mined.yaml and gitignored.
    ids = {task.id for task in tasks if not task.id.startswith("MINE-")}
    assert ids == FROZEN | {"DEMO-PY-01", "DEMO-PY-02"}


def test_manifests_validate_without_frozen_hashes() -> None:
    # A public checkout has no hidden graders; everything else about each task must still hold.
    for task in discover_tasks(Path("tasks")):
        errors = validate_task(task, check_hashes=False, require_graders=False)
        assert errors == [], errors


def test_demo_graders_are_public_and_frozen() -> None:
    for task in select_tasks(Path("tasks"), ["DEMO-PY-01", "DEMO-PY-02"]):
        assert task.has_graders
        assert validate_task(task) == []


def test_a_task_without_graders_is_rejected_unless_allowed(tmp_path) -> None:
    (tmp_path / "private_graders").mkdir()
    shutil.copytree(Path("tasks") / "DEMO-PY-01", tmp_path / "tasks" / "DEMO-PY-01")
    (task,) = select_tasks(tmp_path / "tasks", ["DEMO-PY-01"])
    assert not task.has_graders
    (error,) = validate_task(task)
    assert "missing private grader" in error and "fetch_graders.sh" in error
    assert validate_task(task, require_graders=False) == []
    with pytest.raises(FileNotFoundError, match="DEMO-PY-01"):
        require_graders([task])


def test_doctor_requires_hidden_graders_only_for_a_live_run(tmp_path) -> None:
    from runner.doctor import _offline_checks

    (tmp_path / "private_graders").mkdir()
    shutil.copytree(Path("configs"), tmp_path / "configs")
    shutil.copytree(Path("tasks") / "DEMO-PY-01", tmp_path / "tasks" / "DEMO-PY-01")
    db = f"sqlite:///{tmp_path / 'reg.db'}"

    def suite_hashes(require: bool) -> dict:
        return next(c for c in _offline_checks(tmp_path, db, require_graders=require) if c["check"] == "suite_hashes")

    offline = suite_hashes(False)
    assert offline["ok"] and "graders hidden, not checked here: DEMO-PY-01" in offline["detail"]
    assert not suite_hashes(True)["ok"]
