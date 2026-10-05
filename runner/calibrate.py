"""Task calibration: which tasks carry signal for a series.

A task the series always passes or always fails cannot show a change, so it
only spends usage. After a few repeated runs (``evaluate --repeats 5``) this
reports, per task, the strict pass rate with a Wilson interval, the mean
partial score (fraction of hidden tests / weighted groups passed), and a
verdict: ``keep`` (pass rate inside the band, default 20-80%), ``too_easy``,
``too_hard``, or ``insufficient`` (fewer graded attempts than required).
Infrastructure and invalid-configuration attempts are counted, never graded.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, text

from runner.storage import Store

GRADED = {"quality_pass", "quality_fail"}


@dataclass
class TaskCalibration:
    task: str
    graded: int
    passes: int
    pass_rate: float | None
    ci_low: float | None
    ci_high: float | None
    mean_partial: float | None
    infra: int
    invalid: int
    verdict: str


def wilson(passes: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n <= 0:
        return 0.0, 1.0
    p = passes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def calibrate_rows(
    rows: list[dict[str, Any]],
    *,
    low: float = 0.2,
    high: float = 0.8,
    min_attempts: int = 3,
) -> dict[str, Any]:
    by_task: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_task.setdefault(str(row["task_key"]), []).append(row)
    tasks: list[TaskCalibration] = []
    for task_key in sorted(by_task):
        attempts = by_task[task_key]
        graded = [row for row in attempts if row.get("quality_status") in GRADED]
        passes = sum(1 for row in graded if row.get("quality_status") == "quality_pass")
        partials = [float(row["partial_score"]) for row in graded if row.get("partial_score") is not None]
        infra = sum(1 for row in attempts if row.get("quality_status") in {"infra_fail", "harness_fail"})
        invalid = sum(1 for row in attempts if row.get("quality_status") == "invalid_configuration")
        n = len(graded)
        rate = passes / n if n else None
        lo, hi = wilson(passes, n) if n else (None, None)
        if n < min_attempts:
            verdict = "insufficient"
        elif rate > high:
            verdict = "too_easy"
        elif rate < low:
            verdict = "too_hard"
        else:
            verdict = "keep"
        tasks.append(
            TaskCalibration(
                task=task_key,
                graded=n,
                passes=passes,
                pass_rate=rate,
                ci_low=lo,
                ci_high=hi,
                mean_partial=sum(partials) / len(partials) if partials else None,
                infra=infra,
                invalid=invalid,
                verdict=verdict,
            )
        )
    rated = [item.pass_rate for item in tasks if item.pass_rate is not None]
    kept = [item for item in tasks if item.verdict == "keep"]
    return {
        "band": [low, high],
        "min_attempts": min_attempts,
        "tasks": [asdict(item) for item in tasks],
        "kept": [item.task for item in kept],
        # Suite score is the mean of per-task means, never a pooled attempt rate.
        "series_pass_rate": sum(rated) / len(rated) if rated else None,
        "kept_pass_rate": sum(item.pass_rate for item in kept) / len(kept) if kept else None,
        "attempts": len(rows),
        "graded_attempts": sum(item.graded for item in tasks),
        "infra_attempts": sum(item.infra for item in tasks),
        "invalid_attempts": sum(item.invalid for item in tasks),
    }


def series_run_ids(store: Store, *, model: str, effort: str, track: str) -> list[str]:
    sql = """
    SELECT DISTINCT run_id FROM attempts
    WHERE requested_model = :model AND requested_effort = :effort AND track = :track
    """
    with store.engine.begin() as conn:
        rows = conn.execute(text(sql), {"model": model, "effort": effort, "track": track}).scalars().all()
    return sorted(str(item) for item in rows)


def load_rows(store: Store, run_ids: list[str], *, scientific_only: bool = True) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for run_id in run_ids:
        rows.extend(store.fetch_run_scores(run_id))
    if not scientific_only:
        return rows
    ids = [row["attempt_id"] for row in rows]
    if not ids:
        return rows
    with store.engine.begin() as conn:
        scientific = set(
            conn.execute(
                text("SELECT attempt_id FROM attempts WHERE scientific_data = 1 AND attempt_id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"ids": ids},
            ).scalars()
        )
    return [row for row in rows if row["attempt_id"] in scientific]


def render(report: dict[str, Any]) -> str:
    low, high = report["band"]
    lines = [
        f"{'task':<16} {'graded':>6} {'pass':>5} {'rate':>6}  {'95% CI':<13} {'partial':>7} {'infra':>5}  verdict",
    ]
    for item in report["tasks"]:
        rate = "-" if item["pass_rate"] is None else f"{item['pass_rate']:.0%}"
        ci = "-" if item["ci_low"] is None else f"{item['ci_low']:.0%}-{item['ci_high']:.0%}"
        partial = "-" if item["mean_partial"] is None else f"{item['mean_partial']:.2f}"
        lines.append(
            f"{item['task']:<16} {item['graded']:>6} {item['passes']:>5} {rate:>6}  {ci:<13} {partial:>7} "
            f"{item['infra']:>5}  {item['verdict']}"
        )
    kept = report["kept"]
    lines.append("")
    lines.append(
        f"{len(kept)} of {len(report['tasks'])} tasks inside the {low:.0%}-{high:.0%} band"
        + (f": {', '.join(kept)}" if kept else "")
    )
    if report["series_pass_rate"] is not None:
        lines.append(f"series pass rate (mean of task means): {report['series_pass_rate']:.0%}")
    if report["infra_attempts"] or report["invalid_attempts"]:
        lines.append(
            f"not graded: {report['infra_attempts']} infrastructure, {report['invalid_attempts']} invalid configuration"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="runner calibrate", description=__doc__.split("\n\n")[0])
    parser.add_argument("--runs", default="", help="comma-separated run ids")
    parser.add_argument("--series", default="", help="model/effort/track, e.g. claude-opus-5-5/xhigh/claude_code_product")
    parser.add_argument("--low", type=float, default=0.2)
    parser.add_argument("--high", type=float, default=0.8)
    parser.add_argument("--min-attempts", type=int, default=3)
    parser.add_argument("--any-source", action="store_true", help="include gold/fake/synthetic attempts")
    parser.add_argument("--database-url", default="sqlite:///./artifacts/regression.db")
    parser.add_argument("--out", default="", help="write the JSON report here")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    store = Store(args.database_url)
    run_ids = [item for item in args.runs.split(",") if item]
    if args.series:
        model, effort, track = args.series.split("/")
        run_ids += series_run_ids(store, model=model, effort=effort, track=track)
    if not run_ids:
        parser.error("pass --runs or --series")
    report = calibrate_rows(
        load_rows(store, run_ids, scientific_only=not args.any_source),
        low=args.low,
        high=args.high,
        min_attempts=args.min_attempts,
    )
    report["run_ids"] = run_ids
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2) if args.json else render(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
