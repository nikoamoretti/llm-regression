"""Sequential drift monitoring for each series.

For every model/effort/track series this computes, from graded scientific
attempts only:

- the daily share of attempts that passed and a trailing-window rate, both
  with Wilson 95% intervals;
- a task-paired comparison of the latest window against a reference window
  (mean of per-task differences, bootstrap CI over tasks, sign-flip p-value);
- a risk-adjusted Bernoulli CUSUM (Steiner et al. 2000): each attempt is
  scored against its own task's reference pass rate, so a change in the task
  mix is not mistaken for drift. The threshold is set from an exact
  Markov-chain approximation of the in-control average run length (ARL0), so
  checking the chart every day does not inflate false alarms.

Language follows the README: a drop is "strict task success decreased by N
points on this benchmark", never "the model degraded".
"""

from __future__ import annotations

import argparse
import itertools
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import text

from runner.calibrate import wilson
from runner.storage import Store


@dataclass(frozen=True)
class Attempt:
    when: datetime
    task: str
    passed: bool


def _parse_when(value: str) -> datetime:
    when = datetime.fromisoformat(value)
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def list_series(store: Store, *, scientific_only: bool = True) -> list[tuple[str, str, str]]:
    sql = """
    SELECT DISTINCT requested_model, requested_effort, track FROM attempts
    WHERE quality_status IN ('quality_pass', 'quality_fail') AND completed_at IS NOT NULL
    """ + (" AND scientific_data = 1" if scientific_only else "")
    with store.engine.begin() as conn:
        rows = conn.execute(text(sql)).all()
    return sorted((str(m), str(e), str(t)) for m, e, t in rows if m and e and t)


def load_attempts(
    store: Store, *, model: str, effort: str, track: str, scientific_only: bool = True
) -> list[Attempt]:
    sql = """
    SELECT a.completed_at, t.task_key, a.quality_status FROM attempts a
    JOIN tasks t ON t.task_version_id = a.task_version_id
    WHERE a.requested_model = :model AND a.requested_effort = :effort AND a.track = :track
      AND a.quality_status IN ('quality_pass', 'quality_fail') AND a.completed_at IS NOT NULL
    """ + (" AND a.scientific_data = 1" if scientific_only else "")
    with store.engine.begin() as conn:
        rows = conn.execute(text(sql), {"model": model, "effort": effort, "track": track}).all()
    attempts = [Attempt(_parse_when(str(when)), str(task), status == "quality_pass") for when, task, status in rows]
    return sorted(attempts, key=lambda item: (item.when, item.task))


def _rate(attempts: list[Attempt]) -> dict[str, Any]:
    n = len(attempts)
    passes = sum(item.passed for item in attempts)
    lo, hi = wilson(passes, n) if n else (None, None)
    return {"n": n, "passes": passes, "rate": passes / n if n else None, "lo": lo, "hi": hi}


def daily_rates(attempts: list[Attempt]) -> list[dict[str, Any]]:
    by_day: dict[date, list[Attempt]] = {}
    for item in attempts:
        by_day.setdefault(item.when.date(), []).append(item)
    return [{"date": day.isoformat(), **_rate(items)} for day, items in sorted(by_day.items())]


def rolling_rates(attempts: list[Attempt], window_days: int) -> list[dict[str, Any]]:
    days = sorted({item.when.date() for item in attempts})
    out = []
    for day in days:
        start = day - timedelta(days=window_days - 1)
        window = [item for item in attempts if start <= item.when.date() <= day]
        out.append({"date": day.isoformat(), **_rate(window)})
    return out


def task_rates(attempts: list[Attempt]) -> dict[str, tuple[int, int]]:
    out: dict[str, list[int]] = {}
    for item in attempts:
        counts = out.setdefault(item.task, [0, 0])
        counts[0] += item.passed
        counts[1] += 1
    return {task: (passes, n) for task, (passes, n) in out.items()}


def paired_change(
    reference: list[Attempt], current: list[Attempt], *, seed: int = 20260911, iterations: int = 10_000
) -> dict[str, Any]:
    """Mean of per-task pass-rate differences (current - reference) over shared tasks."""
    ref, cur = task_rates(reference), task_rates(current)
    shared = sorted(set(ref) & set(cur))
    if not shared:
        return {"tasks": 0, "mean_diff": None, "ci": None, "p_value": None, "per_task": []}
    diffs = np.array([cur[t][0] / cur[t][1] - ref[t][0] / ref[t][1] for t in shared])
    rng = np.random.default_rng(seed)
    boots = diffs[rng.integers(0, len(diffs), size=(iterations, len(diffs)))].mean(axis=1)
    observed = abs(diffs.mean())
    if len(diffs) <= 16:
        signs = np.array(list(itertools.product((-1.0, 1.0), repeat=len(diffs))))
    else:
        signs = rng.choice((-1.0, 1.0), size=(iterations, len(diffs)))
    flipped = np.abs((signs * diffs).mean(axis=1))
    p_value = float((flipped >= observed - 1e-12).mean())
    return {
        "tasks": len(shared),
        "mean_diff": float(diffs.mean()),
        "ci": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
        "p_value": p_value,
        "per_task": [
            {
                "task": task,
                "reference": ref[task][0] / ref[task][1],
                "reference_n": ref[task][1],
                "current": cur[task][0] / cur[task][1],
                "current_n": cur[task][1],
                "diff": float(diff),
            }
            for task, diff in zip(shared, diffs)
        ],
    }


def cusum_weights(p: float, odds_ratio: float) -> tuple[float, float]:
    """Score increments (pass, fail) for a task with reference pass probability ``p``."""
    norm = math.log(1 - p + odds_ratio * p)
    return math.log(odds_ratio) - norm, -norm


def average_run_length(
    probs: list[float],
    weights: list[float],
    *,
    odds_ratio: float,
    threshold: float,
    states: int = 400,
    true_probs: list[float] | None = None,
) -> float:
    """Average run length of the risk-adjusted CUSUM (Brook & Evans Markov chain).

    Scores use the reference ``probs``; outcomes follow ``true_probs`` (default:
    the reference, i.e. the in-control ARL).
    """
    width = threshold / states
    levels = np.arange(states) * width
    transition = np.zeros((states, states))
    for p, actual, weight in zip(probs, true_probs or probs, weights):
        for increment, chance in zip(cusum_weights(p, odds_ratio), (actual, 1 - actual)):
            nxt = np.maximum(0.0, levels + increment)
            inside = nxt <= threshold
            index = np.minimum(np.rint(nxt / width).astype(int), states - 1)
            np.add.at(transition, (np.arange(states)[inside], index[inside]), weight * chance)
    run_lengths = np.linalg.solve(np.eye(states) - transition, np.ones(states))
    return float(run_lengths[0])


def threshold_for_arl(probs: list[float], weights: list[float], *, odds_ratio: float, arl0: float) -> float:
    low, high = 0.05, 1.0
    while average_run_length(probs, weights, odds_ratio=odds_ratio, threshold=high) < arl0:
        high *= 2
        if high > 200:
            raise ValueError("cannot reach the requested ARL0")
    for _ in range(40):
        mid = (low + high) / 2
        if average_run_length(probs, weights, odds_ratio=odds_ratio, threshold=mid) < arl0:
            low = mid
        else:
            high = mid
        if high - low < 1e-3:
            break
    return high


def reference_probabilities(reference: list[Attempt]) -> tuple[dict[str, float], float]:
    """Per-task Laplace-smoothed pass probabilities, plus the pooled default."""
    rates = task_rates(reference)
    total_pass = sum(passes for passes, _ in rates.values())
    total_n = sum(n for _, n in rates.values())
    pooled = (total_pass + 1) / (total_n + 2)
    return {task: (passes + 1) / (n + 2) for task, (passes, n) in rates.items()}, pooled


def risk_adjusted_cusum(
    reference: list[Attempt], monitored: list[Attempt], *, odds_ratio: float = 0.5, arl0: float = 2000.0
) -> dict[str, Any]:
    probs, pooled = reference_probabilities(reference)
    counts = task_rates(reference)
    tasks = sorted(probs)
    ref_probs = [probs[t] for t in tasks]
    weights = [counts[t][1] / len(reference) for t in tasks]
    threshold = threshold_for_arl(ref_probs, weights, odds_ratio=odds_ratio, arl0=arl0)
    detect = {
        f"{int(drop * 100)}": average_run_length(
            ref_probs, weights, odds_ratio=odds_ratio, threshold=threshold,
            true_probs=[max(p - drop, 0.001) for p in ref_probs],
        )
        for drop in (0.10, 0.20)
    }
    per_task = sorted(n for _, n in counts.values())
    median_per_task = per_task[len(per_task) // 2]
    statistic, path, alarms = 0.0, [], []
    for item in monitored:
        p = probs.get(item.task, pooled)
        on_pass, on_fail = cusum_weights(p, odds_ratio)
        statistic = max(0.0, statistic + (on_pass if item.passed else on_fail))
        alarm = statistic > threshold
        path.append({"when": item.when.isoformat(), "task": item.task, "passed": item.passed,
                     "expected": p, "statistic": statistic, "alarm": alarm})
        if alarm:
            alarms.append({"when": item.when.isoformat(), "index": len(path) - 1})
            statistic = 0.0  # restart after signalling
    return {
        "odds_ratio": odds_ratio,
        "arl0": arl0,
        "threshold": threshold,
        "attempts_to_detect": detect,
        "median_reference_per_task": median_per_task,
        "path": path,
        "alarms": alarms,
    }


def analyze_series(
    attempts: list[Attempt],
    *,
    series: str,
    window_days: int = 7,
    reference: tuple[date, date] | None = None,
    odds_ratio: float = 0.5,
    arl0: float = 2000.0,
    min_reference: int = 20,
    seed: int = 20260911,
) -> dict[str, Any]:
    report: dict[str, Any] = {"series": series, "window_days": window_days, "attempts": len(attempts)}
    if not attempts:
        return {**report, "status": "insufficient", "message": "no graded attempts"}
    first = attempts[0].when.date()
    ref_start, ref_end = reference or (first, first + timedelta(days=window_days - 1))
    ref = [item for item in attempts if ref_start <= item.when.date() <= ref_end]
    monitored = [item for item in attempts if item.when.date() > ref_end]
    last = attempts[-1].when.date()
    cur_start = max(last - timedelta(days=window_days - 1), ref_end + timedelta(days=1))
    current = [item for item in monitored if item.when.date() >= cur_start]
    report.update(
        {
            "reference": {"start": ref_start.isoformat(), "end": ref_end.isoformat(), **_rate(ref)},
            "current": {"start": cur_start.isoformat(), "end": last.isoformat(), **_rate(current)},
            "daily": daily_rates(attempts),
            "rolling": rolling_rates(attempts, window_days),
        }
    )
    if len(ref) < min_reference:
        return {**report, "status": "insufficient",
                "message": f"reference window has {len(ref)} graded attempts; need {min_reference}"}
    if not monitored:
        return {**report, "status": "insufficient", "message": "no attempts after the reference window yet"}
    report["paired"] = paired_change(ref, current, seed=seed)
    report["cusum"] = risk_adjusted_cusum(ref, monitored, odds_ratio=odds_ratio, arl0=arl0)
    recent = [a for a in report["cusum"]["alarms"] if _parse_when(a["when"]).date() >= cur_start]
    change = report["paired"]["mean_diff"]
    if recent:
        report["status"] = "alarm"
        report["message"] = (
            f"Sustained evidence that strict task success is below the reference on this benchmark "
            f"(first signal {recent[0]['when'][:10]})."
        )
    else:
        report["status"] = "no_alarm"
        report["message"] = "No sustained drop in strict task success relative to the reference in the latest window."
        earlier = report["cusum"]["alarms"]
        if earlier:
            report["message"] += f" Earlier signal(s): {', '.join(a['when'][:10] for a in earlier[:3])}."
    if report["cusum"]["median_reference_per_task"] < 20:
        report["warning"] = (
            f"The reference has a median of {report['cusum']['median_reference_per_task']} attempts per task, so "
            "per-task rates are noisy: expect more false alarms than ARL0 implies, especially if the task mix shifts. "
            "A longer reference window (--reference) fixes this."
        )
    if change is not None:
        lo, hi = report["paired"]["ci"]
        verb = "decreased" if change < 0 else "increased"
        report["message"] += (
            f" Latest window: strict task success {verb} by {abs(change) * 100:.1f} points "
            f"(95% CI {lo * 100:+.1f} to {hi * 100:+.1f}; {report['paired']['tasks']} shared tasks)."
        )
    return report


def _parse_reference(value: str) -> tuple[date, date]:
    start, _, end = value.partition(":")
    return date.fromisoformat(start), date.fromisoformat(end or start)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="runner monitor", description=__doc__.split("\n\n")[0])
    parser.add_argument("--series", action="append", default=[], help="model/effort/track; repeatable (default: all)")
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--reference", default="", help="START:END dates (default: the first window)")
    parser.add_argument("--odds-ratio", type=float, default=0.5, help="drop to detect, as an odds ratio (<1)")
    parser.add_argument("--arl0", type=float, default=2000.0, help="attempts between false alarms when nothing changed")
    parser.add_argument("--min-reference", type=int, default=20)
    parser.add_argument("--any-source", action="store_true", help="include gold/fake attempts (testing only)")
    parser.add_argument("--database-url", default="sqlite:///./artifacts/regression.db")
    parser.add_argument("--out-dir", type=Path, default=Path("artifacts/monitor"))
    args = parser.parse_args(argv)
    if not 0 < args.odds_ratio < 1:
        parser.error("--odds-ratio must be between 0 and 1 (a drop)")
    store = Store(args.database_url)
    scientific = not args.any_source
    chosen = [tuple(item.split("/")) for item in args.series] or list_series(store, scientific_only=scientific)
    reports = []
    for model, effort, track in chosen:
        attempts = load_attempts(store, model=model, effort=effort, track=track, scientific_only=scientific)
        reports.append(
            analyze_series(
                attempts,
                series=f"{model}/{effort}/{track}",
                window_days=args.window_days,
                reference=_parse_reference(args.reference) if args.reference else None,
                odds_ratio=args.odds_ratio,
                arl0=args.arl0,
                min_reference=args.min_reference,
            )
        )
    from runner.monitor_report import write_report

    path = write_report(reports, args.out_dir)
    for report in reports:
        print(f"[{report['status'].upper():>12}] {report['series']}: {report.get('message', '')}")
    print(f"wrote {path}")
    return 1 if any(report["status"] == "alarm" for report in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
