import pytest

from pathlib import Path

from runner.tasks import select_tasks
from runner.test_graders import run_task_selftest

TASKS = [
    "DEMO-PY-01",
    "DEMO-PY-02",
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
]


@pytest.mark.gold
@pytest.mark.parametrize("task_id", TASKS)
def test_gold_and_broken_are_deterministic(task_id: str) -> None:
    # The benchmark tasks' graders are hidden: they exist only after scripts/fetch_graders.sh.
    if not select_tasks(Path.cwd() / "tasks", [task_id])[0].has_graders:
        pytest.skip("hidden graders not fetched (scripts/fetch_graders.sh)")
    errors = run_task_selftest(Path.cwd(), task_id, repeats=2)
    assert errors == [], errors
