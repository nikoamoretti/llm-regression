"""Size a series before measuring it: what drop can a usage budget detect?

Compares two equal windows of attempts (e.g. this week against a reference
week) with a two-sided two-proportion test. Normal approximation; pairing by
task and partial-credit scores help at the margin but do not change the order
of magnitude. Tasks near a 50% pass rate are the informative ones, which is
why ``runner calibrate`` keeps only mid-band tasks.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import NormalDist
from typing import Any

from sqlalchemy import text

from runner.storage import Store

USD_PER_TICK = 1 / 10_000_000_000
DROPS = (0.10, 0.15, 0.20, 0.30)


def _z(alpha: float, power: float) -> float:
    normal = NormalDist()
    return normal.inv_cdf(1 - alpha / 2) + normal.inv_cdf(power)


def min_detectable_drop(n_per_window: int, *, pass_rate: float = 0.5, alpha: float = 0.05, power: float = 0.8) -> float:
    """Smallest pass-rate drop (as a fraction) detectable with n attempts in each window."""
    if n_per_window <= 0:
        return math.inf
    return _z(alpha, power) * math.sqrt(2 * pass_rate * (1 - pass_rate) / n_per_window)


def attempts_needed(drop: float, *, pass_rate: float = 0.5, alpha: float = 0.05, power: float = 0.8) -> int:
    """Attempts per window to detect ``drop`` centered on ``pass_rate``."""
    high, low = min(pass_rate + drop / 2, 1.0), max(pass_rate - drop / 2, 0.0)
    variance = high * (1 - high) + low * (1 - low)
    return math.ceil(_z(alpha, power) ** 2 * variance / drop**2)


def mean_cost_per_attempt(store: Store, *, model: str, effort: str, track: str) -> tuple[float | None, int]:
    sql = """
    SELECT cost_usd_ticks FROM attempts
    WHERE requested_model = :model AND requested_effort = :effort AND track = :track
      AND cost_usd_ticks IS NOT NULL
    """
    with store.engine.begin() as conn:
        ticks = [float(item) for item in conn.execute(text(sql), {"model": model, "effort": effort, "track": track}).scalars()]
    if not ticks:
        return None, 0
    return sum(ticks) / len(ticks) * USD_PER_TICK, len(ticks)


def plan(
    *,
    attempts_per_week: float,
    window_days: float = 7.0,
    pass_rate: float = 0.5,
    tasks: int | None = None,
    alpha: float = 0.05,
    power: float = 0.8,
    cost_per_attempt: float | None = None,
) -> dict[str, Any]:
    per_window = int(attempts_per_week * window_days / 7)
    targets = []
    for drop in DROPS:
        needed = attempts_needed(drop, pass_rate=pass_rate, alpha=alpha, power=power)
        targets.append(
            {
                "drop": drop,
                "attempts_per_window": needed,
                "weeks_of_budget_per_window": needed / attempts_per_week if attempts_per_week else math.inf,
                "repeats_per_task": math.ceil(needed / tasks) if tasks else None,
            }
        )
    return {
        "attempts_per_week": attempts_per_week,
        "window_days": window_days,
        "attempts_per_window": per_window,
        "pass_rate": pass_rate,
        "alpha": alpha,
        "power": power,
        "min_detectable_drop": min_detectable_drop(per_window, pass_rate=pass_rate, alpha=alpha, power=power),
        "repeats_per_task": per_window // tasks if tasks else None,
        "tasks": tasks,
        "cost_per_attempt_usd": cost_per_attempt,
        "targets": targets,
    }


def render(report: dict[str, Any]) -> str:
    lines = []
    if report.get("cost_per_attempt_usd") is not None:
        lines.append(
            f"API-equivalent cost per attempt: ${report['cost_per_attempt_usd']:.2f}"
            + (f" (from {report['cost_samples']} attempts)" if report.get("cost_samples") else "")
        )
    lines.append(
        f"Budget: {report['attempts_per_week']:g} attempts/week -> {report['attempts_per_window']} per "
        f"{report['window_days']:g}-day window"
        + (f" ({report['repeats_per_task']} repeats of each of {report['tasks']} tasks)" if report["tasks"] else "")
    )
    mde = report["min_detectable_drop"]
    lines.append(
        f"Smallest detectable drop ({report['power']:.0%} power, {report['alpha']:.0%} two-sided, "
        f"{report['pass_rate']:.0%} pass rate): "
        + ("not detectable" if math.isinf(mde) or mde >= 1 else f"{mde * 100:.1f} points")
    )
    lines.append("")
    lines.append(f"{'to detect':>10}  {'attempts/window':>15}  {'weeks of budget':>15}  {'repeats/task':>12}")
    for item in report["targets"]:
        weeks = item["weeks_of_budget_per_window"]
        lines.append(
            f"{item['drop'] * 100:>7.0f} pts  {item['attempts_per_window']:>15}  "
            f"{('-' if math.isinf(weeks) else f'{weeks:.1f}'):>15}  {item['repeats_per_task'] or '-':>12}"
        )
    lines.append("")
    lines.append("Each window is compared with a reference window of the same size.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="runner plan", description=__doc__.split("\n\n")[0])
    budget = parser.add_mutually_exclusive_group(required=True)
    budget.add_argument("--attempts-per-week", type=float)
    budget.add_argument("--budget-usd-per-week", type=float, help="API-equivalent dollars; needs a cost per attempt")
    parser.add_argument("--cost-per-attempt", type=float, help="API-equivalent dollars per attempt")
    parser.add_argument("--series", default="", help="model/effort/track: read cost per attempt from the database")
    parser.add_argument("--calibration", type=Path, help="calibration JSON: use its kept tasks and their pass rate")
    parser.add_argument("--pass-rate", type=float)
    parser.add_argument("--tasks", type=int)
    parser.add_argument("--window-days", type=float, default=7.0)
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--power", type=float, default=0.8)
    parser.add_argument("--database-url", default="sqlite:///./artifacts/regression.db")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    pass_rate, tasks = args.pass_rate, args.tasks
    if args.calibration:
        calibration = json.loads(args.calibration.read_text(encoding="utf-8"))
        tasks = tasks or len(calibration.get("kept") or []) or None
        pass_rate = pass_rate if pass_rate is not None else calibration.get("kept_pass_rate")
    pass_rate = 0.5 if pass_rate is None else pass_rate

    cost, samples = args.cost_per_attempt, 0
    if cost is None and args.series:
        model, effort, track = args.series.split("/")
        cost, samples = mean_cost_per_attempt(Store(args.database_url), model=model, effort=effort, track=track)
    if args.attempts_per_week is not None:
        attempts_per_week = args.attempts_per_week
    else:
        if not cost:
            parser.error("--budget-usd-per-week needs --cost-per-attempt or --series with recorded costs")
        attempts_per_week = args.budget_usd_per_week / cost
    report = plan(
        attempts_per_week=attempts_per_week,
        window_days=args.window_days,
        pass_rate=pass_rate,
        tasks=tasks,
        alpha=args.alpha,
        power=args.power,
        cost_per_attempt=cost,
    )
    report["cost_samples"] = samples
    print(json.dumps(report, indent=2) if args.json else render(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
