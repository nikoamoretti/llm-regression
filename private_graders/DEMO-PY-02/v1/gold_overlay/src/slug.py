"""Turn titles into URL slugs."""

from __future__ import annotations


def slugify(text: str) -> str:
    out: list[str] = []
    for ch in text.lower():
        if ch.isascii() and ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")
