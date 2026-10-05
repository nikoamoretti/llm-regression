from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Number:
    value: int


@dataclass(frozen=True)
class Unary:
    op: str
    expr: object


@dataclass(frozen=True)
class Binary:
    op: str
    left: object
    right: object
