"""Paired task-level statistics for longitudinal Codex / GPT-5.6 Sol evaluations.

Primary inference is a seeded hierarchical bootstrap of paired task-level
differences. Wilson intervals remain a descriptive convenience. Ordinary
McNemar is not the primary regression test when attempts are nested in tasks.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

import numpy as np
import pandas as pd


def wilson_interval(successes: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion."""
    if n <= 0:
        raise ValueError("n must be positive")
    if successes < 0 or successes > n:
        raise ValueError("successes must be in [0, n]")
    phat = successes / n
    z2 = z**2
    denom = 1 + z2 / n
    center = (phat + z2 / (2 * n)) / denom
    margin = (z * math.sqrt((phat * (1 - phat) + z2 / (4 * n)) / n)) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def _binom_pmf(k: int, n: int, p: float) -> float:
    return math.comb(n, k) * (p**k) * ((1 - p) ** (n - k))


def exact_mcnemar(regressions: int, improvements: int) -> float:
    """Two-sided exact McNemar / binomial test on discordant pairs."""
    n = regressions + improvements
    if n == 0:
        return 1.0
    k = min(regressions, improvements)
    tail = sum(_binom_pmf(i, n, 0.5) for i in range(k + 1))
    p = min(1.0, 2.0 * tail)
    return p


def paired_transitions(baseline_pass: Iterable[bool], current_pass: Iterable[bool]) -> dict[str, int]:
    pairs = list(zip(baseline_pass, current_pass, strict=True))
    return {
        "pass_pass": sum(b and c for b, c in pairs),
        "pass_fail": sum(b and not c for b, c in pairs),
        "fail_pass": sum((not b) and c for b, c in pairs),
        "fail_fail": sum((not b) and (not c) for b, c in pairs),
    }


def paired_task_bootstrap(
    frame: pd.DataFrame,
    *,
    baseline_label: str,
    current_label: str,
    bootstrap_samples: int = 20_000,
    seed: int = 104729,
) -> dict[str, float]:
    """
    Required columns:
      task_key
      period       baseline_label/current_label
      strict_pass  bool or 0/1

    Tasks, not attempts, are the bootstrap sampling unit.
    """
    required = {"task_key", "period", "strict_pass"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")

    task_period = (
        frame.assign(strict_pass=frame["strict_pass"].astype(float))
        .groupby(["task_key", "period"], as_index=False)["strict_pass"]
        .mean()
    )
    pivot = task_period.pivot(index="task_key", columns="period", values="strict_pass").dropna()
    if baseline_label not in pivot.columns or current_label not in pivot.columns:
        raise ValueError("Both baseline and current periods are required")

    deltas = (pivot[current_label] - pivot[baseline_label]).to_numpy()
    observed = float(deltas.mean())
    rng = np.random.default_rng(seed)
    n_tasks = len(deltas)
    samples = np.empty(bootstrap_samples)
    for i in range(bootstrap_samples):
        indices = rng.integers(0, n_tasks, size=n_tasks)
        samples[i] = deltas[indices].mean()
    lower, upper = np.quantile(samples, [0.025, 0.975])
    return {
        "n_tasks": float(n_tasks),
        "delta": observed,
        "ci_low": float(lower),
        "ci_high": float(upper),
        "probability_degradation": float(np.mean(samples < 0)),
    }


def summarize_rates(successes: int, n: int) -> dict[str, float]:
    low, high = wilson_interval(successes, n)
    return {
        "successes": float(successes),
        "n": float(n),
        "rate": successes / n,
        "wilson_low": low,
        "wilson_high": high,
    }


def relative_change(baseline: float, current: float) -> float:
    if baseline == 0:
        return math.nan
    return (current - baseline) / baseline


def compare_binary_runs(
    baseline_pass: list[bool],
    current_pass: list[bool],
) -> dict[str, Any]:
    if len(baseline_pass) != len(current_pass):
        raise ValueError("Paired runs must have the same task count")
    b = sum(baseline_pass)
    c = sum(current_pass)
    n = len(baseline_pass)
    transitions = paired_transitions(baseline_pass, current_pass)
    return {
        "baseline": summarize_rates(b, n),
        "current": summarize_rates(c, n),
        "absolute_delta": (c / n) - (b / n),
        "relative_delta": relative_change(b / n, c / n),
        "transitions": transitions,
        "mcnemar_p": exact_mcnemar(transitions["pass_fail"], transitions["fail_pass"]),
    }


def ewma(values: Iterable[float], lam: float = 0.2) -> list[float]:
    out: list[float] = []
    current = None
    for value in values:
        current = value if current is None else lam * value + (1 - lam) * current
        out.append(float(current))
    return out


def cusum(values: Iterable[float], target: float, slack: float = 0.0) -> list[float]:
    """One-sided CUSUM of negative drift (quality loss)."""
    s = 0.0
    out = []
    for value in values:
        s = min(0.0, s + (value - target + slack))
        out.append(s)
    return out


def _task_attempt_map(
    frame: pd.DataFrame,
    *,
    task_col: str,
    period_col: str,
    pass_col: str,
    family_col: str,
    baseline_label: str,
    current_label: str,
) -> tuple[dict[str, str], dict[str, dict[str, np.ndarray]]]:
    families: dict[str, str] = {}
    attempts: dict[str, dict[str, np.ndarray]] = {}
    work = frame.copy()
    work[pass_col] = work[pass_col].astype(float)
    if family_col not in work.columns:
        work[family_col] = "default"
    for task, group in work.groupby(task_col, sort=False):
        families[str(task)] = str(group[family_col].iloc[0])
        by_period = {}
        for period, rows in group.groupby(period_col):
            by_period[str(period)] = rows[pass_col].to_numpy(dtype=float)
        if baseline_label in by_period and current_label in by_period:
            attempts[str(task)] = by_period
    return families, attempts


def hierarchical_paired_bootstrap(
    frame: pd.DataFrame,
    *,
    baseline_label: str,
    current_label: str,
    bootstrap_samples: int = 10_000,
    seed: int = 104729,
    family_col: str = "family",
    task_col: str = "task_key",
    period_col: str = "period",
    pass_col: str = "strict_pass",
    min_families_for_hierarchy: int = 3,
) -> dict[str, Any]:
    """Paired task-level bootstrap with optional family→task→attempt nesting.

    For each task:
        task_score = mean(valid attempt successes)
        delta_i = current_task_score - baseline_task_score

    Headline values are the mean of those paired deltas. Pairing is preserved:
    resampling a task always keeps its baseline and current attempt pools.
    """
    required = {task_col, period_col, pass_col}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")

    families, attempts = _task_attempt_map(
        frame,
        task_col=task_col,
        period_col=period_col,
        pass_col=pass_col,
        family_col=family_col,
        baseline_label=baseline_label,
        current_label=current_label,
    )
    if not attempts:
        raise ValueError("Both baseline and current periods are required for paired tasks")

    task_ids = list(attempts)
    observed_deltas = []
    baseline_scores = []
    current_scores = []
    for task in task_ids:
        base = float(attempts[task][baseline_label].mean())
        cur = float(attempts[task][current_label].mean())
        baseline_scores.append(base)
        current_scores.append(cur)
        observed_deltas.append(cur - base)
    observed = float(np.mean(observed_deltas))
    baseline_rate = float(np.mean(baseline_scores))
    current_rate = float(np.mean(current_scores))

    family_to_tasks: dict[str, list[str]] = {}
    for task in task_ids:
        family_to_tasks.setdefault(families[task], []).append(task)
    family_ids = list(family_to_tasks)
    use_family = len(family_ids) >= min_families_for_hierarchy
    method = "family->task->attempt" if use_family else "task->attempt"

    rng = np.random.default_rng(seed)
    samples = np.empty(bootstrap_samples)
    for i in range(bootstrap_samples):
        deltas = []
        if use_family:
            sampled_families = rng.choice(family_ids, size=len(family_ids), replace=True)
            sampled_tasks: list[str] = []
            for family in sampled_families:
                members = family_to_tasks[family]
                sampled_tasks.extend(rng.choice(members, size=len(members), replace=True).tolist())
        else:
            sampled_tasks = rng.choice(task_ids, size=len(task_ids), replace=True).tolist()
        for task in sampled_tasks:
            base_pool = attempts[task][baseline_label]
            cur_pool = attempts[task][current_label]
            base = float(rng.choice(base_pool, size=len(base_pool), replace=True).mean())
            cur = float(rng.choice(cur_pool, size=len(cur_pool), replace=True).mean())
            deltas.append(cur - base)
        samples[i] = float(np.mean(deltas)) if deltas else 0.0

    lower, upper = np.quantile(samples, [0.025, 0.975])
    relative = relative_change(baseline_rate, current_rate)
    return {
        "n_tasks": float(len(task_ids)),
        "n_families": float(len(family_ids)),
        "method": method,
        "baseline_rate": baseline_rate,
        "current_rate": current_rate,
        "delta": observed,
        "absolute_delta_pp": observed * 100.0,
        "relative_delta": relative,
        "ci_low": float(lower),
        "ci_high": float(upper),
        "ci_low_pp": float(lower) * 100.0,
        "ci_high_pp": float(upper) * 100.0,
        "probability_degradation": float(np.mean(samples < 0)),
        "bootstrap_samples": float(bootstrap_samples),
        "seed": float(seed),
        "task_deltas": {task: observed_deltas[idx] for idx, task in enumerate(task_ids)},
        "baseline_task_scores": {task: baseline_scores[idx] for idx, task in enumerate(task_ids)},
        "current_task_scores": {task: current_scores[idx] for idx, task in enumerate(task_ids)},
    }


def paired_headline(boot: dict[str, Any]) -> dict[str, Any]:
    return {
        "baseline_task_balanced_pass_rate": boot["baseline_rate"],
        "current_task_balanced_pass_rate": boot["current_rate"],
        "absolute_delta_pp": boot["absolute_delta_pp"],
        "relative_delta": boot["relative_delta"],
        "ci_95_pp": [boot["ci_low_pp"], boot["ci_high_pp"]],
        "method": boot["method"],
        "n_tasks": int(boot["n_tasks"]),
        "bootstrap_seed": int(boot["seed"]),
    }
