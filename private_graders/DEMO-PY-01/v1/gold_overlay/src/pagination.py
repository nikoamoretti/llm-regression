"""Split a list into fixed-size pages."""

from __future__ import annotations

from typing import Sequence, TypeVar

T = TypeVar("T")


def page_count(total: int, per_page: int) -> int:
    if per_page < 1:
        raise ValueError("per_page must be at least 1")
    return -(-total // per_page)


def page_items(items: Sequence[T], page: int, per_page: int) -> list[T]:
    if per_page < 1 or page < 1:
        raise ValueError("page and per_page must be at least 1")
    start = (page - 1) * per_page
    return list(items[start:start + per_page])
