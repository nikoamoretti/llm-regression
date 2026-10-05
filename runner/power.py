"""Empirical power analysis for paired task-level degradation detection.

This does not claim that any fixed task count is sufficient. After baseline
data exists, it simulates how often a seeded hierarchical bootstrap would
detect a specified drop.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import numpy as np

from runner.identity import assert_single_agent_effort
from runner.statistics import hierarchical_paired_bootstrap
from runner.storage import Store


def _task_success_rates(rows: list[dict[str, Any]]) -> dict[str, list[int]]:
    by_task: dict[str, list[int]] = {}
    for row in rows:
        if row.get("quality_status") not in {"quality_pass", "quality_fail"} and row.get("status") != "completed":
            continue
        if row.get("quality_status") == "infra_fail":
            continue
        if row.get("quality_status") in {"harness_fail", "invalid_configuration"}:
            continue
        passed = int(bool(row.get("strict_pass")))
        if row.get("quality_status") == "quality_pass":
            passed = 1
        elif row.get("quality_status") == "quality_fail":
            passed = 0
        by_task.setdefault(str(row["task_key"]), []).append(passed)
    return by_task


def simulate_power(
    *,
    task_attempts: dict[str, list[int]],
    families: dict[str, str] | None = None,
    minimum_drop_pp: float,
    target_power: float,
    alpha: float,
    candidate_task_counts: list[int],
    candidate_repeats: list[int],
    n_simulations: int = 400,
    bootstrap_samples: int = 400,
    seed: int = 20260911,
) -> dict[str, Any]:
    if not task_attempts:
        raise ValueError("baseline has no valid quality attempts")
    task_ids = list(task_attempts)
    rates = {task: float(np.mean(values)) for task, values in task_attempts.items()}
    families = families or {task: "default" for task in task_ids}
    drop = minimum_drop_pp / 100.0
    rng = np.random.default_rng(seed)
    results = []
    for n_tasks in candidate_task_counts:
        for n_repeats in candidate_repeats:
            detections = 0
            for sim in range(n_simulations):
                chosen = rng.choice(task_ids, size=min(n_tasks, len(task_ids)), replace=len(task_ids) < n_tasks)
                rows = []
                for task in chosen:
                    p = rates[task]
                    base = rng.binomial(1, p, size=n_repeats)
                    current_p = max(0.0, min(1.0, p - drop))
                    cur = rng.binomial(1, current_p, size=n_repeats)
                    for value in base:
                        rows.append(
                            {
                                "task_key": str(task),
                                "family": families.get(str(task), "default"),
                                "period": "baseline",
                                "strict_pass": int(value),
                            }
                        )
                    for value in cur:
                        rows.append(
                            {
                                "task_key": str(task),
                                "family": families.get(str(task), "default"),
                                "period": "current",
                                "strict_pass": int(value),
                            }
                        )
                import pandas as pd

                boot = hierarchical_paired_bootstrap(
                    pd.DataFrame(rows),
                    baseline_label="baseline",
                    current_label="current",
                    bootstrap_samples=bootstrap_samples,
                    seed=int(rng.integers(1, 1_000_000_000)),
                )
                # Detect a degradation: point estimate <= -drop * 0.5 and CI upper < 0
                # at approximately the requested alpha via the 95% interval when alpha=0.05.
                detected = boot["delta"] <= -drop * 0.5 and boot["ci_high"] < 0
                if alpha != 0.05:
                    detected = boot["delta"] < 0 and boot["ci_high"] < 0
                detections += int(detected)
            power = detections / n_simulations
            results.append(
                {
                    "n_tasks": int(n_tasks),
                    "n_repeats": int(n_repeats),
                    "estimated_power": power,
                    "meets_target": power >= target_power,
                    "total_attempts": int(n_tasks) * int(n_repeats),
                }
            )
    results.sort(key=lambda item: (not item["meets_target"], item["n_tasks"], item["n_repeats"], item["total_attempts"]))
    preferred = None
    meeting = [item for item in results if item["meets_target"]]
    if meeting:
        # Prefer more distinct tasks over more repeats when both meet the target.
        meeting.sort(key=lambda item: (-item["n_tasks"], item["n_repeats"], item["total_attempts"]))
        preferred = meeting[0]
    else:
        preferred = max(results, key=lambda item: item["estimated_power"])
    return {
        "minimum_drop_pp": minimum_drop_pp,
        "target_power": target_power,
        "alpha": alpha,
        "n_simulations": n_simulations,
        "bootstrap_samples_per_sim": bootstrap_samples,
        "seed": seed,
        "empirical_tasks": len(task_ids),
        "empirical_mean_task_rate": float(np.mean(list(rates.values()))),
        "candidates": results,
        "recommendation": preferred,
        "notes": [
            "Recommendations increase distinct task coverage when repeats add less information.",
            "This is a simulation from the observed baseline, not a guarantee.",
            "Do not hard-code a task count as sufficient.",
        ],
    }


def render_power(result: dict[str, Any]) -> str:
    rec = result["recommendation"]
    lines = [
        "GPT-5.6 Sol / Codex power analysis",
        "────────────────────────────────────────────────",
        f"Empirical baseline tasks              {result['empirical_tasks']}",
        f"Empirical mean task score             {result['empirical_mean_task_rate']:.3f}",
        f"Target drop                           {result['minimum_drop_pp']:.1f} pp",
        f"Target power                          {result['target_power']:.2f}",
        f"Alpha                                 {result['alpha']:.3f}",
        "",
        "Candidates:",
    ]
    for item in result["candidates"]:
        mark = "✓" if item["meets_target"] else " "
        lines.append(
            f"  [{mark}] tasks={item['n_tasks']:<4} repeats={item['n_repeats']:<3} "
            f"power={item['estimated_power']:.2f} attempts={item['total_attempts']}"
        )
    lines.extend(
        [
            "",
            "Recommendation (prefer more tasks over more repeats):",
            f"  tasks={rec['n_tasks']} repeats={rec['n_repeats']} "
            f"estimated_power={rec['estimated_power']:.2f} meets_target={rec['meets_target']}",
            "────────────────────────────────────────────────",
        ]
    )
    return "\n".join(lines) + "\n"


def load_baseline_attempts(store: Store, baseline_id: str, effort: str | None = None) -> list[dict[str, Any]]:
    baseline = store.get_baseline(baseline_id)
    if baseline is None:
        raise KeyError(f"unknown baseline: {baseline_id}")
    if effort:
        assert_single_agent_effort(effort)
        if baseline.get("effort") and baseline["effort"] != effort:
            raise ValueError(
                f"baseline {baseline['name']} is locked to effort {baseline['effort']}, not {effort}"
            )
    return store.fetch_baseline_scores(baseline["baseline_id"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Power analysis for paired task-level degradation")
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--effort", default="max")
    parser.add_argument("--minimum-drop-pp", type=float, default=8.0)
    parser.add_argument("--target-power", type=float, default=0.80)
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--candidate-task-counts", default="10,20,30,50")
    parser.add_argument("--candidate-repeats", default="1,3,5")
    parser.add_argument("--simulations", type=int, default=400)
    parser.add_argument("--bootstrap-samples", type=int, default=400)
    parser.add_argument("--seed", type=int, default=20260911)
    parser.add_argument("--database-url", default="sqlite:///./artifacts/regression.db")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    store = Store(args.database_url)
    rows = load_baseline_attempts(store, args.baseline, args.effort)
    task_attempts = _task_success_rates(rows)
    families = {row["task_key"]: row.get("category") or row.get("family") or "default" for row in rows}
    result = simulate_power(
        task_attempts=task_attempts,
        families=families,
        minimum_drop_pp=args.minimum_drop_pp,
        target_power=args.target_power,
        alpha=args.alpha,
        candidate_task_counts=[int(item) for item in args.candidate_task_counts.split(",") if item.strip()],
        candidate_repeats=[int(item) for item in args.candidate_repeats.split(",") if item.strip()],
        n_simulations=args.simulations,
        bootstrap_samples=args.bootstrap_samples,
        seed=args.seed,
    )
    result["baseline"] = args.baseline
    result["effort"] = args.effort
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(render_power(result))
        print(json.dumps({"recommendation": result["recommendation"], "effort": args.effort}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
