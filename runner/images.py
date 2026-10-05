"""Build, record, and resolve the pinned container images agents and graders run in.

``python -m runner images build`` builds ``docker/Dockerfile.toolchain`` and
records each image's ID and tool versions in ``artifacts/images.json``. Task
manifests keep their frozen logical image names (``sol-regression-python@sha256:local``
etc.); those resolve to the recorded toolchain image at run time, so v1 task
manifests never change when the environment is rebuilt.

Tasks mined from real repositories need third-party packages. Such a task pins
them in ``environment/requirements.txt`` (hash-locked, e.g. from ``uv export``)
and records its sha256 in the manifest. ``python -m runner images build-env``
installs that file on top of the toolchain (grader image), the Claude Code
image (agent image) and the Cursor image (cursor_agent image) and records them
under ``environments``. A task whose
environment is not built never falls back to the bare toolchain: its tests
would fail on imports and read as model failures.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runner.sandbox import docker_available, docker_image_available

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "docker" / "Dockerfile.toolchain"
ENV_DOCKERFILE = ROOT / "docker" / "Dockerfile.environment"
LOGICAL_TOOLCHAIN_IMAGES = frozenset(
    {"sol-regression-python", "sol-regression-node", "sol-regression-go", "sol-regression-rust"}
)
PROXY_BUILD_ARGS = ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy")
DEFAULT_CLAUDE_CODE_VERSION = "2.1.286"
# Cursor CLI builds are immutable, versioned tarballs; the hash pins this one.
DEFAULT_CURSOR_VERSION = "2026.10.01-e373342"
CURSOR_SHA256 = {"2026.10.01-e373342": "a79726c6e644520e993970be4c45775a6889802b67abe461a677a53219ae28e8"}
# Agent environment kind -> the recorded base image it is layered on.
AGENT_BASES = {"agent": "claude_code", "cursor_agent": "cursor"}


def images_file() -> Path:
    return Path(os.environ.get("LLMREG_IMAGES_FILE") or ROOT / "artifacts" / "images.json")


def load_images(path: Path | None = None) -> dict[str, Any]:
    target = path or images_file()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_images(data: dict[str, Any], path: Path | None = None) -> Path:
    target = path or images_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def host_execution_allowed() -> bool:
    """Explicit opt-in to run scientific attempts without containers."""
    return os.environ.get("LLMREG_ALLOW_HOST") == "1"


def recorded_image(kind: str) -> str | None:
    """Image ID recorded for ``kind`` ("toolchain" / "claude_code" / "cursor") if it still exists locally."""
    record = load_images().get(kind) or {}
    image_id = record.get("id")
    if image_id and docker_image_available(str(image_id)):
        return str(image_id)
    return None


def resolve_task_image(ref: str | None, requirements_sha256: str | None = None) -> str | None:
    """Map a task manifest image reference to a runnable local image, else None."""
    if requirements_sha256:
        return resolve_environment(requirements_sha256, "grader")
    if not ref:
        return None
    name, _, digest = str(ref).partition("@")
    if digest == "sha256:local" or name in LOGICAL_TOOLCHAIN_IMAGES:
        return recorded_image("toolchain")
    return ref if docker_image_available(ref) else None


def task_requirements(manifest: dict[str, Any], task_root: Path) -> tuple[Path, str] | None:
    """The task's pinned requirements file and its manifest sha256, verified; None if it has none."""
    spec = (manifest.get("environment") or {}).get("requirements")
    if not spec:
        return None
    path = (task_root / str(spec["path"])).resolve()
    if task_root.resolve() not in path.parents:
        raise ValueError(f"requirements path escapes the task directory: {spec['path']}")
    expected = str(spec["sha256"])
    if not path.is_file() or _sha256(path) != expected:
        raise ValueError(f"{manifest.get('id')}: {spec['path']} does not match its manifest sha256")
    return path, expected


def resolve_environment(requirements_sha256: str, kind: str) -> str | None:
    """Built ``kind`` ("grader" / "agent" / "cursor_agent") image for a requirements hash, if current and present.

    An environment built on an older toolchain or agent image is stale: the
    base is part of what is measured, so it must be rebuilt, not reused.
    """
    record = (load_images().get("environments") or {}).get(requirements_sha256) or {}
    image = record.get(kind) or {}
    base_kind = "toolchain" if kind == "grader" else AGENT_BASES[kind]
    base = (load_images().get(base_kind) or {}).get("id")
    if not image.get("id") or not base or image.get("base_id") != base:
        return None
    if image.get("dockerfile_sha256") != _sha256(ENV_DOCKERFILE):
        return None
    return str(image["id"]) if docker_image_available(str(image["id"])) else None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inspect_id(tag: str) -> str:
    proc = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", tag],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _run_in(image: str, script: str) -> str:
    proc = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", image, "sh", "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"probe failed in {image}: {(proc.stderr or proc.stdout).strip()[:500]}")
    return proc.stdout.strip()


def _build(
    *,
    target: str | None,
    tag: str,
    build_args: dict[str, str],
    build_network: str | None,
    extra_ca: Path | None,
    dockerfile: Path = DOCKERFILE,
    context_files: dict[str, bytes] | None = None,
) -> None:
    command = ["docker", "build", "-f", str(dockerfile), "-t", tag]
    if target:
        command += ["--target", target]
    if build_network:
        command += ["--network", build_network]
    # Proxy settings are passed by name so their values never appear in argv.
    for key in PROXY_BUILD_ARGS:
        if os.environ.get(key):
            command += ["--build-arg", key]
    for key, value in build_args.items():
        command += ["--build-arg", f"{key}={value}"]
    if extra_ca:
        command += ["--secret", f"id=extra_ca,src={extra_ca}"]
    with tempfile.TemporaryDirectory(prefix="llmreg-build-") as context:
        for name, data in (context_files or {}).items():
            (Path(context) / name).write_bytes(data)
        command.append(context)
        subprocess.run(command, check=True)



def build_environments(
    tasks: list[Any],
    *,
    agent: bool = True,
    build_network: str | None = None,
    extra_ca: Path | None = None,
) -> dict[str, Any]:
    """Build the grader (and agent) images for each distinct requirements file among ``tasks``.

    Agent environments are built for every agent image that is recorded.
    """
    if not docker_available():
        raise SystemExit("docker is not installed")
    data = load_images()
    bases = {"grader": data.get("toolchain") or {}}
    if agent:
        bases["agent"] = data.get("claude_code") or {}
        if data.get("cursor"):
            bases["cursor_agent"] = data["cursor"]
    for kind, base in bases.items():
        if not base.get("tag") or not docker_image_available(str(base.get("id"))):
            raise SystemExit(f"no {kind} base image recorded: run `python -m runner images build` first")
    wanted: dict[str, tuple[Path, list[str]]] = {}
    for task in tasks:
        found = task_requirements(task.manifest, task.root)
        if found:
            path, sha = found
            wanted.setdefault(sha, (path, []))[1].append(task.id)
    environments = data.setdefault("environments", {})
    for sha, (path, task_ids) in sorted(wanted.items()):
        requirements = path.read_bytes()
        record = environments.get(sha) or {}
        for kind, base in bases.items():
            if resolve_environment(sha, kind):
                continue
            layer = hashlib.sha256(f"{sha}:{base['id']}:{_sha256(ENV_DOCKERFILE)}".encode()).hexdigest()
            tag = f"llmreg/env-{kind}:{sha[:12]}-{layer[:12]}"
            _build(
                target=None,
                tag=tag,
                build_args={"BASE_IMAGE": str(base["tag"])},
                build_network=build_network,
                extra_ca=extra_ca,
                dockerfile=ENV_DOCKERFILE,
                context_files={"requirements.txt": requirements},
            )
            image_id = _inspect_id(tag)
            record[kind] = {
                "tag": tag,
                "id": image_id,
                "base_id": base["id"],
                "dockerfile_sha256": _sha256(ENV_DOCKERFILE),
                "packages": _run_in(image_id, "python3 -m pip freeze --disable-pip-version-check").splitlines(),
                "built_at": datetime.now(timezone.utc).isoformat(),
            }
            environments[sha] = record
            save_images(data)
        record["tasks"] = sorted(set(record.get("tasks", [])) | set(task_ids))
    save_images(data)
    return environments


def build_images(
    *,
    claude_code_version: str | None = DEFAULT_CLAUDE_CODE_VERSION,
    cursor_version: str | None = DEFAULT_CURSOR_VERSION,
    build_network: str | None = None,
    extra_ca: Path | None = None,
) -> dict[str, Any]:
    if not docker_available():
        raise SystemExit("docker is not installed")
    dockerfile_sha = _sha256(DOCKERFILE)
    short = dockerfile_sha[:12]
    built_at = datetime.now(timezone.utc).isoformat()
    data = load_images()

    toolchain_tag = f"llmreg/toolchain:{short}"
    _build(target="toolchain", tag=toolchain_tag, build_args={}, build_network=build_network, extra_ca=extra_ca)
    toolchain_id = _inspect_id(toolchain_tag)
    versions = _run_in(
        toolchain_id,
        "python3 --version; python3 -m pytest --version; node --version; go version; rustc --version; cargo --version",
    ).splitlines()
    data["toolchain"] = {
        "tag": toolchain_tag,
        "id": toolchain_id,
        "dockerfile_sha256": dockerfile_sha,
        "built_at": built_at,
        "versions": versions,
    }
    if claude_code_version:
        tag = f"llmreg/claude-code:{claude_code_version}-{short}"
        _build(
            target="claude-code",
            tag=tag,
            build_args={"CLAUDE_CODE_VERSION": claude_code_version},
            build_network=build_network,
            extra_ca=extra_ca,
        )
        image_id = _inspect_id(tag)
        reported = _run_in(image_id, "claude --version")
        if not reported.startswith(claude_code_version):
            raise RuntimeError(f"image reports {reported!r}, expected Claude Code {claude_code_version}")
        data["claude_code"] = {
            "tag": tag,
            "id": image_id,
            "cli_version": reported,
            "requested_version": claude_code_version,
            "dockerfile_sha256": dockerfile_sha,
            "built_at": built_at,
        }
    # Saved before the optional Cursor image, so a failure there never loses the others.
    save_images(data)
    if cursor_version:
        if cursor_version not in CURSOR_SHA256:
            raise SystemExit(f"no pinned sha256 for Cursor CLI {cursor_version}; add it to CURSOR_SHA256")
        tag = f"llmreg/cursor:{cursor_version}-{short}"
        _build(
            target="cursor",
            tag=tag,
            build_args={"CURSOR_VERSION": cursor_version, "CURSOR_SHA256": CURSOR_SHA256[cursor_version]},
            build_network=build_network,
            extra_ca=extra_ca,
        )
        image_id = _inspect_id(tag)
        reported = _run_in(image_id, "agent --version")
        if reported != cursor_version:
            raise RuntimeError(f"image reports {reported!r}, expected Cursor CLI {cursor_version}")
        data["cursor"] = {
            "tag": tag,
            "id": image_id,
            "cli_version": reported,
            "package_sha256": CURSOR_SHA256[cursor_version],
            "dockerfile_sha256": dockerfile_sha,
            "built_at": built_at,
        }
    save_images(data)
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="runner images")
    sub = parser.add_subparsers(dest="action", required=True)
    build = sub.add_parser("build", help="Build the toolchain and agent images and record their IDs")
    build.add_argument("--claude-code-version", default=DEFAULT_CLAUDE_CODE_VERSION)
    build.add_argument("--no-claude-code", action="store_true", help="Skip the Claude Code agent image")
    build.add_argument("--cursor-version", default=DEFAULT_CURSOR_VERSION)
    build.add_argument("--no-cursor", action="store_true", help="Skip the Cursor agent image")
    build.add_argument("--build-network", default=None, help="docker build --network value (e.g. host)")
    build.add_argument("--extra-ca", type=Path, default=None, help="CA bundle for a TLS-inspecting proxy")
    env = sub.add_parser("build-env", help="Build dependency images for tasks that pin requirements")
    env.add_argument("--task", action="append", default=None, help="task id (repeatable; default: all)")
    env.add_argument("--no-agent", action="store_true", help="Build only the grader image")
    env.add_argument("--build-network", default=None)
    env.add_argument("--extra-ca", type=Path, default=None)
    sub.add_parser("show", help="Print the recorded images")
    args = parser.parse_args(argv)
    if args.action == "build-env":
        from runner.tasks import select_tasks

        data = build_environments(
            select_tasks(ROOT / "tasks", args.task),
            agent=not args.no_agent,
            build_network=args.build_network,
            extra_ca=args.extra_ca,
        )
    elif args.action == "build":
        data = build_images(
            claude_code_version=None if args.no_claude_code else args.claude_code_version,
            cursor_version=None if args.no_cursor else args.cursor_version,
            build_network=args.build_network,
            extra_ca=args.extra_ca,
        )
    else:
        data = load_images()
    print(json.dumps(data, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
