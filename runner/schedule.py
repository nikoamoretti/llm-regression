"""Blocked/randomized evaluation schedules.

Do not execute every task for one effort, then every task for the next effort.
Interleave task × effort × repeat so time-of-day/provider-state changes do not
confound a single effort series.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class ScheduledItem:
    task_id: str
    effort: str
    trial_index: int
    order: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_schedule(
    task_ids: Sequence[str],
    efforts: Sequence[str],
    repeats: int,
    seed: int,
) -> list[ScheduledItem]:
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    if not efforts:
        raise ValueError("at least one effort is required")
    items: list[tuple[str, str, int]] = []
    for task_id in task_ids:
        for effort in efforts:
            for trial in range(repeats):
                items.append((task_id, effort, trial))
    rng = random.Random(seed)
    rng.shuffle(items)
    return [
        ScheduledItem(task_id=task_id, effort=effort, trial_index=trial, order=index)
        for index, (task_id, effort, trial) in enumerate(items)
    ]
