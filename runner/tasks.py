"""Load and validate immutable versioned task manifests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from runner.hash_tree import hash_file, hash_tree

SUITE_CLASSES = ("canary", "core", "shadow")
# The benchmark tasks' graders are kept out of the public repository; only the demo tasks' are committed.
GRADERS_HINT = (
    "hidden graders are not in the public repository: fetch them with scripts/fetch_graders.sh "
    "(see private_graders/README.md)"
)


@dataclass(frozen=True)
class TaskSpec:
    root: Path
    repo_root: Path
    manifest: dict[str, Any]

    @property
    def id(self) -> str:
        return str(self.manifest["id"])

    @property
    def version(self) -> str:
        return str(self.manifest["version"])

    @property
    def family(self) -> str:
        return str(self.manifest.get("family") or self.manifest.get("category") or "unknown")

    @property
    def prompt_path(self) -> Path:
        return self.root / self.manifest.get("prompt_file", "prompt.md")

    @property
    def fixture_path(self) -> Path:
        fixture = self.manifest.get("fixture") or {}
        return self.root / fixture.get("path", "fixture")

    @property
    def private_grader_path(self) -> Path:
        explicit = self.manifest.get("private_grader_path")
        if explicit:
            return (self.repo_root / explicit).resolve()
        return (self.repo_root / "private_graders" / self.id / self.version).resolve()

    @property
    def grader_path(self) -> Path:
        return self.private_grader_path

    @property
    def has_graders(self) -> bool:
        return self.private_grader_path.is_dir()

    @property
    def gold_patch(self) -> Path:
        return self.private_grader_path / "gold.patch"

    @property
    def negatives_dir(self) -> Path:
        return self.private_grader_path / "negatives"

    def prompt_text(self) -> str:
        return self.prompt_path.read_text(encoding="utf-8")

    def computed_hashes(self) -> dict[str, str]:
        hashes = {
            "prompt_sha256": hash_file(self.prompt_path),
            "fixture_sha256": hash_tree(self.fixture_path),
            "grader_sha256": hash_tree(self.grader_path),
        }
        if self.gold_patch.exists():
            hashes["gold_sha256"] = hash_file(self.gold_patch)
        return hashes

    def content_identity(self) -> str:
        hashes = self.computed_hashes()
        return hashes["fixture_sha256"] + ":" + hashes["grader_sha256"] + ":" + hashes["prompt_sha256"]


def _repo_root_from(path: Path) -> Path:
    for candidate in [path, *path.parents]:
        if (candidate / "private_graders").exists() or (candidate / "runner").exists():
            return candidate
    return path if path.name != "tasks" else path.parent


def load_task(task_dir: Path, repo_root: Path | None = None) -> TaskSpec:
    manifest_path = task_dir / "task.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError(f"Invalid manifest: {manifest_path}")
    root = task_dir.resolve()
    repo = (repo_root or _repo_root_from(root)).resolve()
    return TaskSpec(root=root, repo_root=repo, manifest=manifest)


def discover_tasks(tasks_root: Path) -> list[TaskSpec]:
    tasks_root = tasks_root.resolve()
    repo_root = _repo_root_from(tasks_root)
    found: list[TaskSpec] = []
    for path in sorted(tasks_root.iterdir()):
        if not path.is_dir() or path.name.startswith("_"):
            continue
        if (path / "task.yaml").exists():
            found.append(load_task(path, repo_root))
            continue
        versions = sorted(
            [child for child in path.iterdir() if child.is_dir() and (child / "task.yaml").exists()],
            key=lambda item: item.name,
        )
        for version_dir in versions:
            found.append(load_task(version_dir, repo_root))
    return found


def select_tasks(tasks_root: Path, keys: list[str] | None = None, version: str | None = None) -> list[TaskSpec]:
    discovered = discover_tasks(tasks_root)
    by_id: dict[str, TaskSpec] = {}
    for task in discovered:
        if version and task.version != version:
            continue
        # Prefer the latest directory name if multiple versions exist and no pin.
        previous = by_id.get(task.id)
        if previous is None or task.version >= previous.version:
            by_id[task.id] = task
    if keys is None:
        return list(by_id.values())
    missing = [key for key in keys if key not in by_id]
    if missing:
        hint = ""
        if any(key.startswith("MINE-") for key in missing):
            hint = (
                "; mined tasks are materialized locally: "
                "python -m runner mine --batch configs/mined.yaml --repos-dir <dir with the source clones>"
            )
        raise KeyError(f"Unknown tasks: {missing}{hint}")
    return [by_id[key] for key in keys]


def require_graders(tasks: list[TaskSpec]) -> None:
    missing = sorted(task.id for task in tasks if not task.has_graders)
    if missing:
        raise FileNotFoundError(f"no graders for {missing}: {GRADERS_HINT}")


def validate_task(task: TaskSpec, *, check_hashes: bool = True, require_graders: bool = True) -> list[str]:
    """Check one task. Without its graders it fails, unless ``require_graders`` is off (public CI)."""
    errors: list[str] = []
    required = [
        "id",
        "version",
        "family",
        "prompt_file",
        "fixture",
        "grader",
        "scoring",
    ]
    for key in required:
        if key not in task.manifest:
            errors.append(f"{task.id}: missing {key}")
    if not task.prompt_path.exists():
        errors.append(f"{task.id}: missing prompt")
    if not task.fixture_path.exists():
        errors.append(f"{task.id}: missing fixture")
    if not task.has_graders:
        if require_graders:
            errors.append(f"{task.id}: missing private grader at {task.grader_path}; {GRADERS_HINT}")
    else:
        if not task.gold_patch.exists():
            errors.append(f"{task.id}: missing gold.patch")
        if not task.negatives_dir.exists() or not any(task.negatives_dir.glob("*.patch")):
            errors.append(f"{task.id}: missing negative patches")
    for field in ("task_review_status", "task_reviewed_at", "task_review_notes"):
        if field not in task.manifest:
            errors.append(f"{task.id}: missing {field}")
    if check_hashes:
        computed = (
            task.computed_hashes() if task.has_graders else {"fixture_sha256": hash_tree(task.fixture_path)}
        )
        expected_fixture = task.manifest.get("fixture", {}).get("sha256")
        if expected_fixture and expected_fixture != "REPLACE_AFTER_FREEZE":
            if computed["fixture_sha256"] != expected_fixture:
                errors.append(
                    f"{task.id}: fixture hash {computed['fixture_sha256']} "
                    f"!= manifest {expected_fixture}"
                )
        expected_grader = task.manifest.get("grader", {}).get("sha256")
        if task.has_graders and expected_grader and expected_grader != "REPLACE_AFTER_FREEZE":
            if computed["grader_sha256"] != expected_grader:
                errors.append(
                    f"{task.id}: grader hash {computed['grader_sha256']} "
                    f"!= manifest {expected_grader}"
                )
    return errors


def suite_hash(tasks: list[TaskSpec], suite_name: str, suite_version: str) -> str:
    from runner.hash_tree import canonical_hash

    payload = {
        "suite": suite_name,
        "version": suite_version,
        "members": [
            {
                "id": task.id,
                "version": task.version,
                "hashes": task.computed_hashes(),
            }
            for task in tasks
        ],
    }
    return canonical_hash(payload)
