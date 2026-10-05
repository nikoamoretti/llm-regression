"""Deterministic SHA-256 hashing for frozen fixture and grader trees."""

from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Any, Iterable

IGNORED = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    ".mypy_cache",
    "target",
    ".cargo",
}


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_text(text: str) -> str:
    return hash_bytes(text.encode("utf-8"))


def canonical_json(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_hash(obj: object) -> str:
    return hash_bytes(canonical_json(obj).encode("utf-8"))


def _iter_files(root: pathlib.Path, extra_ignored: Iterable[str] = ()) -> list[pathlib.Path]:
    ignored = IGNORED | set(extra_ignored)
    files = [
        path
        for path in root.rglob("*")
        if path.is_file() and not any(part in ignored for part in path.relative_to(root).parts)
    ]
    return sorted(files, key=lambda p: p.relative_to(root).as_posix())


def hash_tree(root: pathlib.Path, extra_ignored: Iterable[str] = ()) -> str:
    digest = hashlib.sha256()
    root = root.resolve()
    for path in _iter_files(root, extra_ignored):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def hash_file(path: pathlib.Path) -> str:
    return hash_bytes(path.read_bytes())


def stable_request_hash(value: dict[str, Any]) -> str:
    """SHA-256 of a request body with sorted object keys."""
    return canonical_hash(value)
