"""Pick today's slice of a suite, so a daily run costs a fraction of the full suite.

Tasks are ordered hardest first (then by id) and dealt into ``slots`` groups
round-robin; day ``d`` runs group ``d mod slots``, so every task runs once every
``slots`` days and each day gets a fair share of the hard ones. Composite tasks
(chains of commits, most of a run's cost) instead run once a week each, one per
weekday from Monday. The drift monitor adjusts for task mix, so a rotating subset
is still a valid series.

    python -m runner rotation --suite product --slots 4            # today's tasks
    python -m runner rotation --suite product --slots 4 --date 2026-10-04
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from pathlib import Path

from runner.config import load_suites
from runner.tasks import select_tasks

ROOT = Path(__file__).resolve().parents[1]
DIFFICULTY_ORDER = {"hard": 0, "medium": 1, "easy": 2}


def groups(tasks: list[tuple[str, str, bool]], slots: int) -> list[list[str]]:
    """``tasks`` = (id, difficulty, composite); returns ``slots`` groups covering every task once."""
    ordered = sorted(tasks, key=lambda t: (DIFFICULTY_ORDER.get(t[1], 1), not t[2], t[0]))
    out: list[list[str]] = [[] for _ in range(slots)]
    for index, (task_id, _, _) in enumerate(ordered):
        out[index % slots].append(task_id)
    return [sorted(group) for group in out]


def weekly_slot(composites: list[str], day: date) -> list[str]:
    """Composite tasks run once a week each, one per weekday from Monday on."""
    ordered = sorted(composites)
    return [ordered[day.weekday()]] if day.weekday() < len(ordered) else []


def todays_tasks(suite_name: str, slots: int, day: date, root: Path = ROOT, *, weekly_composites: bool = True) -> list[str]:
    suite = load_suites(root / "configs" / "suites.yaml")[suite_name]
    specs = select_tasks(root / "tasks", suite.tasks)
    tasks = [(t.id, str(t.manifest.get("difficulty", "medium")),
              bool((t.manifest.get("source") or {}).get("commits"))) for t in specs]
    if slots <= 1:
        return [t[0] for t in tasks]
    if not weekly_composites:
        return groups(tasks, slots)[day.toordinal() % slots]
    # The composite tasks cost most of a run: they leave the daily rotation and run weekly.
    composites = [t[0] for t in tasks if t[2]]
    daily = groups([t for t in tasks if not t[2]], slots)[day.toordinal() % slots]
    return sorted(daily + weekly_slot(composites, day))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="runner rotation", description=__doc__.split("\n\n")[0])
    parser.add_argument("--suite", default="product")
    parser.add_argument("--slots", type=int, default=4)
    parser.add_argument("--date", type=date.fromisoformat, default=None, help="UTC date (default: today)")
    parser.add_argument("--composites-daily", action="store_true", help="rotate composite tasks daily too")
    args = parser.parse_args(argv)
    day = args.date or datetime.now(timezone.utc).date()
    print(",".join(todays_tasks(args.suite, args.slots, day, weekly_composites=not args.composites_daily)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
