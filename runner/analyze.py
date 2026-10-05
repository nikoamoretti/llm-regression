"""Compare a current evaluation against frozen/rolling baselines."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from runner.alerts import efficiency_alert, quality_alert, render_status_line
from runner.statistics import compare_binary_runs, paired_task_bootstrap, relative_change
from runner.storage import Store


def scores_frame(rows: list[dict[str, Any]], period: str) -> pd.DataFrame:
    evaluable = [
        row
        for row in rows
        if row.get("status") == "completed" and row.get("strict_pass") is not None
    ]
    return pd.DataFrame(
        [
            {
                "task_key": row["task_key"],
                "period": period,
                "strict_pass": bool(row["strict_pass"]),
                "partial_score": row.get("partial_score"),
                "cost_usd_ticks": row.get("cost_usd_ticks"),
                "wall_time_ms": row.get("wall_time_ms"),
                "reasoning_tokens": row.get("reasoning_tokens"),
            }
            for row in evaluable
        ]
    )


def analyze_runs(
    store: Store,
    current_run_id: str,
    baseline_run_id: str | None = None,
    fail_on_confirmed: bool = False,
) -> dict[str, Any]:
    current_rows = store.fetch_run_scores(current_run_id)
    current = scores_frame(current_rows, "current")
    if current.empty:
        raise ValueError(f"No evaluable attempts in run {current_run_id}")

    current_rate = float(current["strict_pass"].mean())
    result: dict[str, Any] = {
        "current_run_id": current_run_id,
        "baseline_run_id": baseline_run_id,
        "current_strict_pass": current_rate,
        "current_n": int(current.groupby("task_key").ngroups),
    }

    if baseline_run_id:
        baseline = scores_frame(store.fetch_run_scores(baseline_run_id), "baseline")
        if baseline.empty:
            raise ValueError(f"No evaluable attempts in baseline {baseline_run_id}")
        frame = pd.concat([baseline, current], ignore_index=True)
        boot = paired_task_bootstrap(frame, baseline_label="baseline", current_label="current")
        base_task = baseline.groupby("task_key")["strict_pass"].mean()
        cur_task = current.groupby("task_key")["strict_pass"].mean()
        paired = base_task.index.intersection(cur_task.index)
        comparison = compare_binary_runs(
            [bool(base_task[k] >= 0.5) for k in paired],
            [bool(cur_task[k] >= 0.5) for k in paired],
        )
        result.update(
            {
                "baseline_strict_pass": float(base_task.mean()),
                "paired_delta": boot["delta"],
                "ci_low": boot["ci_low"],
                "ci_high": boot["ci_high"],
                "n_tasks": int(boot["n_tasks"]),
                "relative_delta": relative_change(float(base_task.mean()), current_rate),
                "mcnemar_p": comparison["mcnemar_p"],
                "transitions": comparison["transitions"],
            }
        )
        decision = quality_alert(
            absolute_delta=boot["delta"],
            ci_high=boot["ci_high"],
            n_tasks=int(boot["n_tasks"]),
        )
        cost_ratio = _median_ratio(baseline, current, "cost_usd_ticks")
        wall_ratio = _median_ratio(baseline, current, "wall_time_ms")
        reason_ratio = _median_ratio(baseline, current, "reasoning_tokens")
        extra = efficiency_alert(
            cost_ratio=cost_ratio,
            wall_ratio=wall_ratio,
            reasoning_ratio=reason_ratio,
            quality_improved=boot["delta"] > 0,
        )
        result["alert"] = {
            "severity": decision.severity,
            "status_line": render_status_line(decision),
            "reason": decision.reason,
            "efficiency": [item.reason for item in extra],
        }
        if fail_on_confirmed and decision.severity == "critical":
            result["failed"] = True
    return result


def _median_ratio(baseline: pd.DataFrame, current: pd.DataFrame, column: str) -> float | None:
    b = baseline.loc[baseline["strict_pass"] == True, column].dropna()  # noqa: E712
    c = current.loc[current["strict_pass"] == True, column].dropna()  # noqa: E712
    if b.empty or c.empty or float(b.median()) == 0:
        return None
    return float(c.median()) / float(b.median())


def render_markdown(result: dict[str, Any]) -> str:
    lines = [
        "GPT-5.6 Sol / Codex evaluation",
        "────────────────────────────────────────────────",
        f"Baseline strict pass                 {result.get('baseline_strict_pass', float('nan')):.1%}"
        if "baseline_strict_pass" in result
        else "Baseline strict pass                 n/a",
        f"Current strict pass                  {result['current_strict_pass']:.1%}",
    ]
    if "paired_delta" in result:
        lines.extend(
            [
                f"Paired change                       {result['paired_delta']*100:+.1f} pp",
                f"95% task-bootstrap CI           [{result['ci_low']*100:.1f}, {result['ci_high']*100:.1f}]",
                f"Relative degradation                 {result.get('relative_delta', float('nan')):.1%}",
                f"McNemar p                           {result['mcnemar_p']:.3f}",
                f"Regression status                 {result['alert']['status_line']}",
            ]
        )
    lines.append("────────────────────────────────────────────────")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze Codex / GPT-5.6 Sol regression runs")
    parser.add_argument("--current", required=True)
    parser.add_argument("--baseline")
    parser.add_argument("--rolling-days", type=int, default=28)
    parser.add_argument("--fail-on-confirmed-regression", action="store_true")
    parser.add_argument("--database-url", default="sqlite:///./artifacts/regression.db")
    parser.add_argument("--out", default="artifacts/summary.json")
    args = parser.parse_args(argv)
    store = Store(args.database_url)
    result = analyze_runs(
        store,
        args.current,
        args.baseline,
        fail_on_confirmed=args.fail_on_confirmed_regression,
    )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    markdown = render_markdown(result)
    Path(args.out).with_suffix(".md").write_text(markdown, encoding="utf-8")
    print(markdown)
    return 1 if result.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
