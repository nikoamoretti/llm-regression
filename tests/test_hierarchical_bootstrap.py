from __future__ import annotations

import pandas as pd
import pytest

from runner.statistics import hierarchical_paired_bootstrap


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _task_rows(task: str, family: str, period: str, values: list[int]) -> list[dict]:
    return [
        {"task_key": task, "family": family, "period": period, "strict_pass": value}
        for value in values
    ]


def test_bootstrap_no_difference() -> None:
    rows = []
    for task in ["A", "B", "C", "D"]:
        rows.extend(_task_rows(task, "f1", "baseline", [1, 1]))
        rows.extend(_task_rows(task, "f1", "current", [1, 1]))
    result = hierarchical_paired_bootstrap(_frame(rows), baseline_label="baseline", current_label="current", bootstrap_samples=2000, seed=1)
    assert result["delta"] == 0
    assert result["ci_low"] <= 0 <= result["ci_high"]


def test_bootstrap_known_20pp_regression() -> None:
    rows = []
    for task in [f"T{i}" for i in range(10)]:
        rows.extend(_task_rows(task, "f1", "baseline", [1, 1, 1, 1, 1]))
        rows.extend(_task_rows(task, "f1", "current", [1, 1, 1, 1, 0]))
    result = hierarchical_paired_bootstrap(_frame(rows), baseline_label="baseline", current_label="current", bootstrap_samples=3000, seed=2)
    assert result["delta"] == pytest.approx(-0.2)
    assert result["ci_high"] < 0


def test_bootstrap_obvious_improvement() -> None:
    rows = []
    for task in ["A", "B", "C"]:
        rows.extend(_task_rows(task, "f1", "baseline", [0, 0]))
        rows.extend(_task_rows(task, "f1", "current", [1, 1]))
    result = hierarchical_paired_bootstrap(_frame(rows), baseline_label="baseline", current_label="current", bootstrap_samples=2000, seed=3)
    assert result["delta"] == 1.0
    assert result["ci_low"] > 0


def test_high_within_task_stochasticity() -> None:
    rows = []
    for task in ["A", "B", "C", "D"]:
        rows.extend(_task_rows(task, "f1", "baseline", [1, 0, 1, 0, 1, 0]))
        rows.extend(_task_rows(task, "f1", "current", [1, 0, 1, 0, 1, 0]))
    result = hierarchical_paired_bootstrap(_frame(rows), baseline_label="baseline", current_label="current", bootstrap_samples=2000, seed=4)
    assert result["delta"] == 0
    assert result["ci_low"] < 0 < result["ci_high"]


def test_clustered_families_uses_family_hierarchy() -> None:
    rows = []
    for task in ["A1", "A2"]:
        rows.extend(_task_rows(task, "alpha", "baseline", [1, 1]))
        rows.extend(_task_rows(task, "alpha", "current", [0, 0]))
    for task in ["B1", "B2"]:
        rows.extend(_task_rows(task, "beta", "baseline", [1]))
        rows.extend(_task_rows(task, "beta", "current", [1]))
    for task in ["C1", "C2"]:
        rows.extend(_task_rows(task, "gamma", "baseline", [1]))
        rows.extend(_task_rows(task, "gamma", "current", [1]))
    result = hierarchical_paired_bootstrap(_frame(rows), baseline_label="baseline", current_label="current", bootstrap_samples=2000, seed=5)
    assert result["method"] == "family->task->attempt"
    assert result["n_families"] == 3


def test_unequal_repeat_counts() -> None:
    rows = []
    rows.extend(_task_rows("A", "f", "baseline", [1] * 8))
    rows.extend(_task_rows("A", "f", "current", [0] * 2))
    rows.extend(_task_rows("B", "f", "baseline", [1]))
    rows.extend(_task_rows("B", "f", "current", [1]))
    result = hierarchical_paired_bootstrap(_frame(rows), baseline_label="baseline", current_label="current", bootstrap_samples=1500, seed=6)
    assert result["n_tasks"] == 2
    assert result["baseline_task_scores"]["A"] == 1
    assert result["current_task_scores"]["A"] == 0


def test_missing_infra_attempts_are_omitted_from_quality_frame() -> None:
    rows = []
    rows.extend(_task_rows("A", "f", "baseline", [1, 1]))
    rows.extend(_task_rows("A", "f", "current", [1]))
    rows.extend(_task_rows("B", "f", "baseline", [0]))
    rows.extend(_task_rows("B", "f", "current", [0]))
    result = hierarchical_paired_bootstrap(_frame(rows), baseline_label="baseline", current_label="current", bootstrap_samples=1000, seed=7)
    assert result["n_tasks"] == 2


def test_all_pass_and_all_fail_tasks() -> None:
    rows = []
    rows.extend(_task_rows("pass", "f", "baseline", [1, 1]))
    rows.extend(_task_rows("pass", "f", "current", [1, 1]))
    rows.extend(_task_rows("fail", "f", "baseline", [0, 0]))
    rows.extend(_task_rows("fail", "f", "current", [0, 0]))
    result = hierarchical_paired_bootstrap(_frame(rows), baseline_label="baseline", current_label="current", bootstrap_samples=1000, seed=8)
    assert result["baseline_rate"] == 0.5
    assert result["delta"] == 0
