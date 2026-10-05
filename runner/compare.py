"""Compare a current evaluation against a locked baseline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from runner.alerts import evaluate_alert_policy, load_alert_policy, render_status_line
from runner.baselines import compare_identities, show_baseline
from runner.statistics import hierarchical_paired_bootstrap, paired_headline, relative_change
from runner.status import reliability_metrics
from runner.storage import Store


def _rows_to_frame(rows: list[dict[str, Any]], period: str) -> pd.DataFrame:
    records = []
    for row in rows:
        status = row.get("quality_status") or row.get("status")
        if status in {"infra_fail", "harness_fail", "invalid_configuration", "infrastructure_error"}:
            continue
        if row.get("strict_pass") is None and status not in {"quality_pass", "quality_fail", "completed"}:
            continue
        passed = bool(row.get("strict_pass"))
        if status == "quality_pass":
            passed = True
        elif status == "quality_fail":
            passed = False
        records.append(
            {
                "task_key": row["task_key"],
                "family": row.get("category") or row.get("family") or "unknown",
                "period": period,
                "strict_pass": passed,
                "quality_status": status,
                "partial_score": row.get("partial_score"),
            }
        )
    return pd.DataFrame(records)


def _reliability(rows: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = []
    for row in rows:
        status = row.get("quality_status")
        if status:
            statuses.append(status)
        elif row.get("status") == "completed" and row.get("strict_pass") is not None:
            statuses.append("quality_pass" if row["strict_pass"] else "quality_fail")
        elif row.get("status") in {"infrastructure_error", "infra_fail"}:
            statuses.append("infra_fail")
        else:
            statuses.append(row.get("status") or "harness_fail")
    return reliability_metrics(statuses)


def compare_runs(
    store: Store,
    *,
    baseline_name: str,
    current_run_id: str,
    force: bool = False,
    bootstrap_samples: int = 10_000,
    seed: int = 104729,
    policy_path: Path | None = None,
) -> dict[str, Any]:
    baseline = show_baseline(store, baseline_name)
    current_run = store.get_run(current_run_id)
    if current_run is None:
        raise KeyError(f"unknown run {current_run_id}")
    current_identity = {
        "track": current_run.get("track") or baseline["track"],
        "model": current_run.get("requested_model") or baseline["model"],
        "effort": current_run.get("requested_effort") or baseline["effort"],
        "client_mode": current_run.get("client_mode") or baseline.get("client_mode", "latest"),
        "suite_id": baseline["suite_id"],
        "suite_version": baseline.get("suite_version", ""),
        "suite_hash": current_run.get("suite_hash") or baseline.get("suite_hash", ""),
        "grading_protocol": baseline.get("grading_protocol", "hidden_final_state"),
        "auth_surface": current_run.get("auth_surface") or baseline.get("auth_surface", "chatgpt"),
        "provider": current_run.get("provider") or baseline.get("provider") or "",
        "protocol_version": current_run.get("protocol_version") or baseline.get("protocol_version") or "1.0.0",
        "tool_protocol_version": current_run.get("tool_protocol_version")
        or baseline.get("tool_protocol_version")
        or "2.0.0",
        "scientific_data": current_run.get("scientific_data"),
    }
    compatibility = compare_identities(baseline, current_identity, force=force)
    baseline_rows = store.fetch_baseline_scores(baseline["baseline_id"])
    current_rows = store.fetch_run_scores(current_run_id)
    baseline_frame = _rows_to_frame(baseline_rows, "baseline")
    current_frame = _rows_to_frame(current_rows, "current")
    if baseline_frame.empty or current_frame.empty:
        raise ValueError("baseline or current has no valid quality attempts")
    frame = pd.concat([baseline_frame, current_frame], ignore_index=True)
    boot = hierarchical_paired_bootstrap(
        frame,
        baseline_label="baseline",
        current_label="current",
        bootstrap_samples=bootstrap_samples,
        seed=seed,
    )
    headline = paired_headline(boot)
    policy = load_alert_policy(policy_path)
    alert = evaluate_alert_policy(
        policy,
        absolute_delta=boot["delta"],
        ci_high=boot["ci_high"],
        consecutive_windows=1,
    )
    largest = []
    for task, delta in sorted(boot["task_deltas"].items(), key=lambda item: item[1]):
        largest.append(
            {
                "task": task,
                "family": next(
                    (row.get("category") for row in current_rows if row["task_key"] == task),
                    "unknown",
                ),
                "baseline_task_score": boot["baseline_task_scores"][task],
                "current_task_score": boot["current_task_scores"][task],
                "delta": delta,
                "attempts": int((current_frame["task_key"] == task).sum()),
            }
        )
    result = {
        "canonical": compatibility["canonical"],
        "non_canonical": compatibility["non_canonical"],
        "compatibility_problems": compatibility["problems"],
        "baseline": baseline["name"],
        "current_run_id": current_run_id,
        "track": current_identity["track"],
        "model": current_identity["model"],
        "effort": current_identity["effort"],
        "headline": headline,
        "bootstrap": {key: boot[key] for key in boot if key not in {"task_deltas", "baseline_task_scores", "current_task_scores"}},
        "largest_regressions": largest[:10],
        "reliability_current": _reliability(current_rows),
        "reliability_baseline": _reliability(baseline_rows),
        "alert": {
            "severity": alert.severity,
            "status_line": render_status_line(alert),
            "reason": alert.reason,
            "label": alert.label,
        },
        "language": (
            f"Evidence of regression in GPT-5.6 Sol / Codex / {current_identity['effort']} "
            "under this frozen benchmark"
            if alert.severity in {"warning", "confirmed_regression", "critical"}
            else "No configured regression evidence on this frozen benchmark"
        ),
        "bootstrap_seed": seed,
        "relative_delta": relative_change(boot["baseline_rate"], boot["current_rate"]),
    }
    store.add_analysis(
        {
            "baseline_id": baseline["baseline_id"],
            "current_run_id": current_run_id,
            "track": current_identity["track"],
            "model": current_identity["model"],
            "effort": current_identity["effort"],
            "method": boot["method"],
            "bootstrap_seed": seed,
            "payload": result,
        }
    )
    return result


def render_markdown(result: dict[str, Any]) -> str:
    head = result["headline"]
    lines = [
        "GPT-5.6 Sol / Codex comparison",
        "────────────────────────────────────────────────",
        f"Baseline                              {result['baseline']}",
        f"Current run                           {result['current_run_id']}",
        f"Track / effort                        {result['track']} / {result['effort']}",
        f"Canonical comparison                  {result['canonical']}",
        f"Baseline task-balanced pass rate      {head['baseline_task_balanced_pass_rate']:.1%}",
        f"Current task-balanced pass rate       {head['current_task_balanced_pass_rate']:.1%}",
        f"Absolute delta                        {head['absolute_delta_pp']:+.1f} pp",
        f"Relative delta                        {head['relative_delta'] if head['relative_delta'] == head['relative_delta'] else float('nan'):.1%}"
        if False
        else f"Relative delta                        {result['relative_delta']}",
        f"95% paired CI                         [{head['ci_95_pp'][0]:+.1f}, {head['ci_95_pp'][1]:+.1f}] pp",
        f"Method                                {head['method']}",
        f"Distinct tasks                        {head['n_tasks']}",
        f"Status                                {result['alert']['status_line']}",
        result["language"],
        "────────────────────────────────────────────────",
    ]
    if result["non_canonical"]:
        lines.insert(5, "NON-CANONICAL COMPARISON: " + "; ".join(result["compatibility_problems"]))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare a run to a locked baseline")
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--current", required=True)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=104729)
    parser.add_argument("--database-url", default="sqlite:///./artifacts/regression.db")
    parser.add_argument("--out", default="artifacts/summary.json")
    args = parser.parse_args(argv)
    store = Store(args.database_url)
    result = compare_runs(
        store,
        baseline_name=args.baseline,
        current_run_id=args.current,
        force=args.force,
        bootstrap_samples=args.bootstrap_samples,
        seed=args.seed,
    )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    markdown = render_markdown(result)
    Path(args.out).with_suffix(".md").write_text(markdown, encoding="utf-8")
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
