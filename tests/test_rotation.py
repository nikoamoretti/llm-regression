from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from runner.evaluate import evaluate
from runner.rotation import groups, todays_tasks, weekly_slot


def test_every_task_runs_once_per_cycle_and_hard_work_is_spread() -> None:
    tasks = [(f"H{i}", "hard", i < 4) for i in range(8)] + [(f"M{i}", "medium", False) for i in range(16)]
    dealt = groups(tasks, 4)
    assert sorted(t for g in dealt for t in g) == sorted(t[0] for t in tasks)
    assert [len(g) for g in dealt] == [6, 6, 6, 6]
    # One composite (H0-H3) and two hard tasks per day.
    assert [sum(t in {"H0", "H1", "H2", "H3"} for t in g) for g in dealt] == [1, 1, 1, 1]
    assert [sum(t.startswith("H") for t in g) for g in dealt] == [2, 2, 2, 2]


def test_a_suite_rotates_deterministically_by_date(tmp_path) -> None:
    # A self-contained suite over the committed canary tasks (mined tasks are not in a fresh checkout).
    root = Path(__file__).resolve().parents[1]
    canary = yaml.safe_load((root / "configs" / "suites.yaml").read_text())["canary"]
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "suites.yaml").write_text(yaml.safe_dump({"rot": {**canary, "class": "canary"}}))
    (tmp_path / "tasks").symlink_to(root / "tasks")
    start = date(2026, 10, 3)
    cycle = [todays_tasks("rot", 4, start + timedelta(days=d), root=tmp_path) for d in range(4)]
    assert len({tuple(day) for day in cycle}) == 4
    assert todays_tasks("rot", 4, start + timedelta(days=4), root=tmp_path) == cycle[0]
    assert sorted(t for day in cycle for t in day) == sorted(canary["tasks"])
    assert sorted(todays_tasks("rot", 1, start, root=tmp_path)) == sorted(canary["tasks"])


def test_evaluate_runs_only_the_requested_subset(tmp_path) -> None:
    run_ids = evaluate(Path.cwd(), "demo", efforts=["xhigh"], source="gold", tracks=["model_only"],
                       models=["grok-4.6"], repeats=1, artifact_dir=tmp_path / "arts",
                       database_url=f"sqlite:///{tmp_path / 'reg.db'}", only_tasks=["DEMO-PY-01", "DEMO-PY-02"])
    from sqlalchemy import text

    from runner.storage import Store

    store = Store(f"sqlite:///{tmp_path / 'reg.db'}")
    with store.engine.begin() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM attempts WHERE run_id = :r"),
                            {"r": run_ids["grok-4.6/xhigh/model_only"]}).scalar() == 2
    with pytest.raises(KeyError, match="not in suite"):
        evaluate(Path.cwd(), "demo", efforts=["xhigh"], source="gold", tracks=["model_only"],
                 models=["grok-4.6"], repeats=1, artifact_dir=tmp_path / "arts2",
                 database_url=f"sqlite:///{tmp_path / 'reg2.db'}", only_tasks=["NOPE-01"])


def test_composite_tasks_run_once_a_week_each() -> None:
    composites = ["C3", "C1", "C2", "C4"]
    monday = date(2026, 10, 5)
    week = [weekly_slot(composites, monday + timedelta(days=d)) for d in range(7)]
    assert week == [["C1"], ["C2"], ["C3"], ["C4"], [], [], []]
