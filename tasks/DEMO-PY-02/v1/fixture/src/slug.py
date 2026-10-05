"""Turn titles into URL slugs."""

from __future__ import annotations


def slugify(text: str) -> str:
    out = []
    for ch in text.lower():
        # BUG: every separator becomes its own hyphen, and the ends are not trimmed.
        out.append(ch if ch.isascii() and ch.isalnum() else "-")
    return "".join(out)
