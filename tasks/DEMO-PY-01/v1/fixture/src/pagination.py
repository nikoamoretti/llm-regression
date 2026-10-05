"""Split a list into fixed-size pages."""

from __future__ import annotations

from typing import Sequence, TypeVar

T = TypeVar("T")


def page_count(total: int, per_page: int) -> int:
    if per_page < 1:
        raise ValueError("per_page must be at least 1")
    # BUG: a partly full last page is dropped.
    return total // per_page


def page_items(items: Sequence[T], page: int, per_page: int) -> list[T]:
    if per_page < 1 or page < 1:
        raise ValueError("page and per_page must be at least 1")
    # BUG: pages are treated as 0-based.
    start = page * per_page
    return list(items[start:start + per_page])
