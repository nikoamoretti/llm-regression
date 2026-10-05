from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from runner.evaluate import evaluate
from runner.storage import Store


def test_parallel_workers_record_every_attempt_once(tmp_path) -> None:
    db = f"sqlite:///{tmp_path / 'reg.db'}"
    run_ids = evaluate(Path.cwd(), "demo", efforts=["xhigh"], source="gold", tracks=["model_only"],
                       models=["grok-4.6"], repeats=2, workers=4, artifact_dir=tmp_path / "arts",
                       database_url=db)
    store = Store(db)
    with store.engine.connect() as conn:
        rows = conn.execute(
            text("SELECT task_version_id, trial_index, quality_status FROM attempts WHERE run_id = :r"),
            {"r": run_ids["grok-4.6/xhigh/model_only"]},
        ).all()
    assert len(rows) == 4
    assert len({(task, trial) for task, trial, _ in rows}) == 4
    assert {status for _, _, status in rows} == {"quality_pass"}
