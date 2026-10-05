"""Blocked randomization and model-independent pair keys."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Sequence

from runner.protocols import pairing_protocol_version


def pair_key(
    *,
    suite_hash: str,
    task_id: str,
    task_version: str,
    track: str,
    effort: str,
    repeat_index: int,
    schedule_seed: int,
    protocol_version: str | None = None,
) -> str:
    """Stable pair identity. Must not include model, date, or provider."""
    payload = {
        "suite_hash": suite_hash,
        "task_id": task_id,
        "task_version": task_version,
        "track": track,
        "effort": effort,
        "repeat_index": int(repeat_index),
        "schedule_seed": int(schedule_seed),
        "protocol_version": protocol_version or pairing_protocol_version(),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"pair-{digest[:24]}"


def blocked_model_order(
    models: Sequence[str],
    *,
    pair_key_value: str,
    schedule_seed: int,
) -> list[str]:
    """Deterministically shuffle model order inside a pair. Pair key is independent of this order."""
    if len(models) <= 1:
        return list(models)
    material = f"{schedule_seed}|{pair_key_value}|blocked-model-order".encode("utf-8")
    digest = hmac.new(b"harness-blocked-randomization", material, hashlib.sha256).digest()
    indexed = list(enumerate(models))
    # Rank by successive 8-byte slices so the shuffle is stable and model-name-independent
    # except as the input list order, which is then reordered by the digest.
    scores: list[tuple[int, int]] = []
    for i, _name in indexed:
        start = (i * 8) % max(1, len(digest) - 7)
        scores.append((int.from_bytes(digest[start : start + 8], "big"), i))
    scores.sort()
    return [models[i] for _score, i in scores]


@dataclass(frozen=True)
class PairSlot:
    pair_key: str
    task_id: str
    task_version: str
    track: str
    effort: str
    repeat_index: int
    models: tuple[str, ...]
