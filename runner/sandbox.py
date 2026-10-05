"""Ephemeral task sandboxes: Docker when available, local fallback otherwise."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from runner.hash_tree import hash_tree
from runner.patch import init_workspace_git


@dataclass
class SandboxResult:
    returncode: int
    stdout: str
    stderr: str
    backend: str


def docker_available() -> bool:
    return shutil.which("docker") is not None


@lru_cache(maxsize=None)
def docker_image_available(image: str) -> bool:
    """True only when the daemon is reachable and ``image`` resolves locally.

    Placeholder references such as ``name@sha256:local`` and images that were
    never built fail ``docker image inspect`` and use the local fallback.
    """
    if not docker_available():
        return False
    try:
        proc = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def container_user_args() -> list[str]:
    """Run containers as the host user so workspace files stay owned by them."""
    if hasattr(os, "getuid"):
        return ["--user", f"{os.getuid()}:{os.getgid()}"]
    return []


def copy_tree(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest, dirs_exist_ok=True)


class TaskSandbox:
    def __init__(
        self,
        fixture: Path,
        work_root: Path | None = None,
        image: str | None = None,
        network: bool = False,
        env: Mapping[str, str] | None = None,
        verify_sha256: str | None = None,
        materialize_commands: list[list[str]] | None = None,
        requirements_sha256: str | None = None,
    ) -> None:
        self.fixture = fixture.resolve()
        self.image = image
        self.requirements_sha256 = requirements_sha256
        self._resolved_image: str | None = None
        self._image_resolved = False
        self.network = network
        self.env = dict(env or {})
        self.verify_sha256 = verify_sha256
        self.materialize_commands = materialize_commands or []
        if work_root is not None:
            Path(work_root).mkdir(parents=True, exist_ok=True)
        self._tmp = Path(tempfile.mkdtemp(prefix="sol-task-", dir=work_root))
        self.workspace = self._tmp / "workspace"
        self.grader_mount = self._tmp / "grader"

    def materialize(self) -> Path:
        copy_tree(self.fixture, self.workspace)
        for command in self.materialize_commands:
            subprocess.run(command, cwd=self.workspace, check=True)
        actual = hash_tree(self.workspace)
        if (
            self.verify_sha256
            and self.verify_sha256 not in {None, "", "REPLACE_AFTER_FREEZE"}
            and actual != self.verify_sha256
        ):
            # Fixture hash is of the source tree before optional generation.
            source_hash = hash_tree(self.fixture)
            if source_hash != self.verify_sha256:
                raise RuntimeError(
                    "Fixture hash mismatch: "
                    f"expected {self.verify_sha256}, source={source_hash}, workspace={actual}"
                )
        init_workspace_git(self.workspace)
        return self.workspace

    def resolved_image(self) -> str | None:
        """Runnable local image for this task (logical names resolve via images.json)."""
        if not self._image_resolved:
            from runner.images import resolve_task_image

            self._resolved_image = resolve_task_image(self.image, self.requirements_sha256)
            self._image_resolved = True
        return self._resolved_image

    def uses_docker(self) -> bool:
        return self.resolved_image() is not None

    def mount_grader(self, grader: Path) -> Path:
        copy_tree(grader, self.grader_mount)
        return self.grader_mount

    def run(
        self,
        command: list[str],
        timeout_seconds: int = 120,
        extra_env: Mapping[str, str] | None = None,
    ) -> SandboxResult:
        env = {
            # Caller toolchains (e.g. Node >= 22.6 for --experimental-strip-types)
            # win over system defaults; system dirs remain as a fallback.
            "PATH": os.pathsep.join(
                part
                for part in (os.environ.get("PATH", ""), "/usr/local/bin:/usr/bin:/bin")
                if part
            ),
            "HOME": str(self._tmp / "model-home"),
            "TZ": self.env.get("TZ", "UTC"),
            "LANG": self.env.get("LANG", "C.UTF-8"),
            "LC_ALL": self.env.get("LC_ALL", "C.UTF-8"),
            "PYTHONHASHSEED": self.env.get("PYTHONHASHSEED", "410728"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "WORKSPACE": str(self.workspace),
            "GRADER": str(self.grader_mount),
        }
        env["CARGO_TARGET_DIR"] = str(self._tmp / "cargo-target")
        env["GOCACHE"] = str(self._tmp / "gocache")
        for key in (
            "CARGO_HOME",
            "RUSTUP_HOME",
            "RUSTUP_TOOLCHAIN",
            "GOROOT",
            "GOPATH",
            "GOTOOLCHAIN",
            "NODE_PATH",
            "NPM_CONFIG_CACHE",
        ):
            if key in os.environ:
                env[key] = os.environ[key]
        # rustup derives these from HOME, which is replaced above; keep the
        # caller's toolchain reachable when they are not set explicitly.
        for key, default in (("RUSTUP_HOME", ".rustup"), ("CARGO_HOME", ".cargo")):
            real = Path.home() / default
            if key not in env and real.is_dir():
                env[key] = str(real)
        env.update(self.env)
        if extra_env:
            env.update(extra_env)

        if self.uses_docker():
            return self._run_docker(command, timeout_seconds, env)
        return self._run_local(command, timeout_seconds, env)

    def _run_local(
        self,
        command: list[str],
        timeout_seconds: int,
        env: dict[str, str],
    ) -> SandboxResult:
        rendered = [self._render(part) for part in command]
        proc = subprocess.run(
            rendered,
            cwd=self.workspace,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        return SandboxResult(proc.returncode, proc.stdout, proc.stderr, "local")

    def _run_docker(
        self,
        command: list[str],
        timeout_seconds: int,
        env: dict[str, str],
    ) -> SandboxResult:
        forwarded = {
            "TZ": env.get("TZ", "UTC"),
            "LANG": env.get("LANG", "C.UTF-8"),
            "LC_ALL": env.get("LC_ALL", "C.UTF-8"),
            "PYTHONHASHSEED": env.get("PYTHONHASHSEED", "410728"),
            "PYTHONDONTWRITEBYTECODE": "1",
            **{key: value for key, value in self.env.items() if key not in {"PATH", "HOME"}},
        }
        name = f"llmreg-grade-{uuid.uuid4().hex[:12]}"
        docker_cmd = [
            "docker",
            "run",
            "--rm",
            "--name",
            name,
            "--network",
            "none" if not self.network else "bridge",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            "2g",
            "--cpus",
            "2",
            "--pids-limit",
            "256",
            *container_user_args(),
            # Toolchain state (HOME, caches) lives under /tmp in the image.
            "--tmpfs",
            "/tmp:rw,exec,size=1g",
            *[arg for key, value in sorted(forwarded.items()) for arg in ("-e", f"{key}={value}")],
            "-v",
            f"{self.workspace}:/workspace:rw",
            # A disposable per-attempt copy: frozen graders write scratch files
            # (go.mod, cargo target) beside themselves, as they do on the host.
            "-v",
            f"{self.grader_mount}:/grader:rw",
            "-w",
            "/workspace",
            str(self.resolved_image()),
            *[self._render(part, docker=True) for part in command],
        ]
        try:
            proc = subprocess.run(
                docker_cmd,
                env=env,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        finally:
            # A timed-out `docker run` leaves its container running.
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
        return SandboxResult(proc.returncode, proc.stdout, proc.stderr, "docker")

    def _render(self, part: str, docker: bool = False) -> str:
        workspace = "/workspace" if docker else str(self.workspace)
        grader = "/grader" if docker else str(self.grader_mount)
        return part.replace("/workspace", workspace).replace("/grader", grader)

    def cleanup(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)
