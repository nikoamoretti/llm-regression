import pytest

from pathlib import Path

from runner.test_graders import run_task_selftest


@pytest.mark.gold
@pytest.mark.parametrize(
    "task_id",
    [
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
    ],
)
def test_gold_and_broken_are_deterministic(task_id: str) -> None:
    errors = run_task_selftest(Path.cwd(), task_id, repeats=2)
    assert errors == [], errors
