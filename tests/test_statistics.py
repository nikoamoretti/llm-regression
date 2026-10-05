from runner.statistics import (
    compare_binary_runs,
    exact_mcnemar,
    paired_task_bootstrap,
    relative_change,
    wilson_interval,
)
import pandas as pd


def test_wilson_matches_documented_50_task_example() -> None:
    low, high = wilson_interval(40, 50)
    assert 0.669 < low < 0.671
    assert 0.887 < high < 0.889
    low, high = wilson_interval(34, 50)
    assert 0.541 < low < 0.543
    assert 0.792 < high < 0.793


def test_mcnemar_10_vs_4() -> None:
    p = exact_mcnemar(10, 4)
    assert 0.179 < p < 0.181


def test_documented_transition_is_not_conclusive() -> None:
    baseline = [True] * 40 + [False] * 10
    current = [True] * 30 + [False] * 10 + [True] * 4 + [False] * 6
    # Rebuild from the documented 30/10/4/6 pairing instead of the naive lists.
    baseline = [True] * 30 + [True] * 10 + [False] * 4 + [False] * 6
    current = [True] * 30 + [False] * 10 + [True] * 4 + [False] * 6
    result = compare_binary_runs(baseline, current)
    assert result["baseline"]["successes"] == 40
    assert result["current"]["successes"] == 34
    assert abs(result["absolute_delta"] + 0.12) < 1e-9
    assert abs(result["relative_delta"] + 0.15) < 1e-9
    assert result["mcnemar_p"] > 0.05


def test_relative_change() -> None:
    assert abs(relative_change(0.8, 0.68) + 0.15) < 1e-9


def test_bootstrap_uses_tasks_not_attempts() -> None:
    rows = []
    for task in ["A", "B", "C"]:
        rows.append({"task_key": task, "period": "baseline", "strict_pass": 1})
        rows.append({"task_key": task, "period": "baseline", "strict_pass": 1})
        rows.append({"task_key": task, "period": "current", "strict_pass": 0})
        rows.append({"task_key": task, "period": "current", "strict_pass": 0})
    frame = pd.DataFrame(rows)
    result = paired_task_bootstrap(
        frame,
        baseline_label="baseline",
        current_label="current",
        bootstrap_samples=2000,
        seed=1,
    )
    assert result["n_tasks"] == 3
    assert result["delta"] == -1.0
    assert result["ci_high"] < 0
