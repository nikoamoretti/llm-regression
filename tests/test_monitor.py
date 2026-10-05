from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from runner.monitor import (
    Attempt,
    analyze_series,
    average_run_length,
    cusum_weights,
    main as monitor_main,
    paired_change,
    risk_adjusted_cusum,
    threshold_for_arl,
)
from runner.monitor_report import render_html

START = datetime(2026, 9, 1, 9, tzinfo=timezone.utc)
TASKS = {f"T{i:02d}": p for i, p in enumerate([0.3, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.35, 0.5])}


def _series(days: int, per_day: int, *, drop_from: int | None = None, drop: float = 0.0, seed: int = 7,
            tasks: dict[str, float] = TASKS, mix=None) -> list[Attempt]:
    rng = random.Random(seed)
    out = []
    for day in range(days):
        for k in range(per_day):
            pool = mix(day) if mix else sorted(tasks)
            task = rng.choice(pool)
            p = tasks[task] - (drop if drop_from is not None and day >= drop_from else 0.0)
            out.append(Attempt(START + timedelta(days=day, minutes=17 * k), task, rng.random() < p))
    return out


def test_cusum_increments_penalize_failures_and_drift_down_in_control() -> None:
    on_pass, on_fail = cusum_weights(0.6, 0.5)
    assert on_pass < 0 < on_fail
    # In control the expected increment is negative, so the statistic hugs zero.
    assert 0.6 * on_pass + 0.4 * on_fail < 0


def test_markov_run_length_matches_simulation() -> None:
    probs, weights = [0.25, 0.5, 0.75], [1 / 3] * 3
    h = threshold_for_arl(probs, weights, odds_ratio=0.5, arl0=150)
    assert average_run_length(probs, weights, odds_ratio=0.5, threshold=h) == pytest.approx(150, rel=0.05)
    rng = np.random.default_rng(3)
    wp = np.array([cusum_weights(p, 0.5)[0] for p in probs])
    wf = np.array([cusum_weights(p, 0.5)[1] for p in probs])
    paths = 3000
    s, done, run = np.zeros(paths), np.zeros(paths, bool), np.zeros(paths)
    t = 0
    while not done.all() and t < 5000:
        t += 1
        k = rng.integers(0, 3, paths)
        x = rng.random(paths) < np.array(probs)[k]
        s = np.where(done, s, np.maximum(0, s + np.where(x, wp[k], wf[k])))
        hit = ~done & (s > h)
        run[hit], done = t, done | hit
    assert run.mean() == pytest.approx(150, rel=0.12)


def test_threshold_grows_with_the_false_alarm_horizon() -> None:
    probs, weights = [0.5], [1.0]
    assert threshold_for_arl(probs, weights, odds_ratio=0.5, arl0=100) < threshold_for_arl(
        probs, weights, odds_ratio=0.5, arl0=1000
    )


def test_a_real_drop_alarms_and_a_stable_series_does_not() -> None:
    dropped = analyze_series(_series(28, 15, drop_from=21, drop=0.25), series="m/xhigh/claude_code_product")
    assert dropped["status"] == "alarm"
    assert "strict task success decreased" in dropped["message"] and "degraded" not in dropped["message"]
    assert dropped["paired"]["mean_diff"] < -0.1 and dropped["paired"]["ci"][1] < 0
    stable = analyze_series(_series(28, 15), series="m/xhigh/claude_code_product")
    assert stable["status"] == "no_alarm"
    assert stable["paired"]["ci"][0] < 0 < stable["paired"]["ci"][1]


def _reference_then_monitored(seed: int, per_task: int, monitored: int, hard_share: float):
    rng = random.Random(seed)
    when = START
    reference = []
    for task in sorted(TASKS):
        for _ in range(per_task):
            reference.append(Attempt(when, task, rng.random() < TASKS[task]))
            when += timedelta(minutes=1)
    later, when = [], START + timedelta(days=8)
    for _ in range(monitored):
        task = rng.choice(["T00", "T01", "T08"]) if rng.random() < hard_share else rng.choice(sorted(TASKS))
        later.append(Attempt(when, task, rng.random() < TASKS[task]))
        when += timedelta(minutes=1)
    return reference, later


def test_a_shift_toward_hard_tasks_is_mostly_absorbed_by_risk_adjustment() -> None:
    # Same per-task pass rates throughout; the monitored period draws only hard tasks.
    adjusted = naive = 0
    for seed in range(8):
        reference, later = _reference_then_monitored(seed, per_task=30, monitored=420, hard_share=1.0)
        adjusted += len(risk_adjusted_cusum(reference, later, arl0=2000)["alarms"])
        pooled = lambda items: [Attempt(a.when, "ALL", a.passed) for a in items]  # noqa: E731
        naive += len(risk_adjusted_cusum(pooled(reference), pooled(later), arl0=2000)["alarms"])
    assert naive >= 20 and adjusted <= naive * 0.3


def test_thin_references_are_flagged_and_detection_speed_is_reported() -> None:
    report = analyze_series(_series(28, 15, drop_from=21, drop=0.25), series="m/xhigh/claude_code_product")
    assert "warning" in report and "per-task rates are noisy" in report["warning"]
    detect = report["cusum"]["attempts_to_detect"]
    assert detect["20"] < detect["10"] < report["cusum"]["arl0"] / 5


def test_paired_change_is_exact_for_few_tasks() -> None:
    ref = [Attempt(START, t, ok) for t, ok in [("A", True), ("A", True), ("B", True), ("B", False)]]
    cur = [Attempt(START + timedelta(days=9), t, ok) for t, ok in [("A", False), ("A", True), ("B", False), ("B", False)]]
    result = paired_change(ref, cur)
    assert result["tasks"] == 2
    assert result["mean_diff"] == pytest.approx(-0.5)
    assert result["p_value"] == pytest.approx(0.5)  # 2 of 4 sign patterns are as extreme


def test_insufficient_data_is_reported_not_guessed() -> None:
    thin = analyze_series(_series(3, 4), series="m/e/t")
    assert thin["status"] == "insufficient" and "need 20" in thin["message"]
    one_window = analyze_series(_series(7, 10), series="m/e/t")
    assert one_window["status"] == "insufficient" and "no attempts after" in one_window["message"]
    assert analyze_series([], series="m/e/t")["status"] == "insufficient"


def test_report_renders_both_layouts_and_escapes_task_names() -> None:
    attempts = _series(21, 12, drop_from=14, drop=0.3)
    attempts.append(Attempt(START + timedelta(days=20, hours=5), "</script><b>x", False))
    report = analyze_series(attempts, series="claude-opus-5-5/xhigh/claude_code_product")
    page = render_html([report], generated_at=START)
    assert 'id="rate-0-wide"' in page and 'id="rate-0-narrow"' in page and 'id="cusum-0-narrow"' in page
    assert "</script><b>" not in page
    payload = page.split('id="viz-data">', 1)[1].split("</script>", 1)[0]
    assert json.loads(payload)["charts"]
    assert page.index('id="viz-tooltip"') < page.rindex("</div>")  # inside the themed root
    assert "prefers-color-scheme: dark" in page and ':root[data-theme="dark"]' in page
    assert "Alarm" in page and "claude-opus-5-5/<wbr>xhigh/<wbr>claude_code_product" in page


def test_monitor_cli_reads_the_database(tmp_path, capsys) -> None:
    from runner.evaluate import evaluate

    db = f"sqlite:///{tmp_path / 'reg.db'}"
    evaluate(Path.cwd(), "demo", efforts=["xhigh"], source="gold", tracks=["model_only"], models=["grok-4.6"],
             repeats=2, artifact_dir=tmp_path / "arts", database_url=db)
    out = tmp_path / "monitor"
    assert monitor_main(["--any-source", "--database-url", db, "--out-dir", str(out)]) == 0
    reports = json.loads((out / "monitor.json").read_text())
    assert [r["series"] for r in reports] == ["grok-4.6/xhigh/model_only"]
    assert reports[0]["status"] == "insufficient" and reports[0]["attempts"] == 4
    assert (out / "index.html").exists()
    # Without --any-source, gold attempts are not scientific and nothing is monitored.
    assert monitor_main(["--database-url", db, "--out-dir", str(out)]) == 0
    assert json.loads((out / "monitor.json").read_text()) == []
    assert "INSUFFICIENT" in capsys.readouterr().out
