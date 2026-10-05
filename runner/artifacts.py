"""Local artifact store with content-addressed objects plus logical paths."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from runner.hash_tree import hash_bytes, hash_text


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.objects = self._objects_root()
        self.objects.mkdir(parents=True, exist_ok=True)

    def _objects_root(self) -> Path:
        # Prefer artifacts/objects even when this store is artifacts/runs.
        if self.root.name in {"runs", "private", "dashboard"}:
            return self.root.parent / "objects" / "sha256"
        if self.root.name == "artifacts":
            return self.root / "objects" / "sha256"
        return self.root / "objects" / "sha256"

    def _object_path(self, digest: str) -> Path:
        return self.objects / digest[:2] / digest

    def _put_object(self, digest: str, content: bytes) -> Path:
        path = self._object_path(digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(content)
        return path

    def write_text(self, relative: str, content: str) -> dict[str, str]:
        encoded = content.encode("utf-8")
        digest = hash_text(content)
        object_path = self._put_object(digest, encoded)
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {
            "uri": str(path),
            "sha256": digest,
            "object_uri": str(object_path),
        }

    def write_json(self, relative: str, payload: Any) -> dict[str, str]:
        encoded = json.dumps(payload, indent=2, sort_keys=True, default=str)
        return self.write_text(relative, encoded)

    def write_bytes(self, relative: str, content: bytes) -> dict[str, str]:
        digest = hash_bytes(content)
        object_path = self._put_object(digest, content)
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return {
            "uri": str(path),
            "sha256": digest,
            "object_uri": str(object_path),
        }

    def verify(self, relative: str, expected_sha256: str) -> str:
        path = self.root / relative
        if not path.exists():
            raise FileNotFoundError(f"artifact missing: {path}")
        actual = hash_bytes(path.read_bytes())
        if actual != expected_sha256:
            raise ValueError(
                f"artifact hash mismatch for {relative}: expected {expected_sha256}, got {actual}"
            )
        return actual
