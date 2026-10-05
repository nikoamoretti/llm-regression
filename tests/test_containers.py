"""Container integration tests. Run with LLMREG_DOCKER_TESTS=1 after `runner images build`."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FAKE = Path(__file__).resolve().parent / "fakes" / "claude"
MODEL = "claude-opus-5-5"

pytestmark = pytest.mark.skipif(
    os.environ.get("LLMREG_DOCKER_TESTS") != "1", reason="set LLMREG_DOCKER_TESTS=1 to run container tests"
)


@pytest.fixture
def images(monkeypatch) -> dict:
    from runner.images import load_images

    path = Path(os.environ.get("LLMREG_DOCKER_IMAGES_FILE") or ROOT / "artifacts" / "images.json")
    monkeypatch.setenv("LLMREG_IMAGES_FILE", str(path))
    data = load_images(path)
    if not (data.get("toolchain") and data.get("claude_code")):
        pytest.skip("run `python -m runner images build` first")
    return data


def test_all_canary_task_images_resolve_to_the_toolchain_container(images) -> None:
    from runner.sandbox import TaskSandbox
    from runner.tasks import select_tasks

    for task in select_tasks(ROOT / "tasks", None):
        sandbox = TaskSandbox(task.fixture_path, image=task.manifest["environment"]["image"])
        try:
            assert sandbox.resolved_image() == images["toolchain"]["id"], task.id
        finally:
            sandbox.cleanup()


@pytest.mark.parametrize("task_id", ["BUG-TS-01", "STATE-GO-01", "NAV-RUST-01", "DATA-PY-01"])
def test_graders_validate_inside_the_container(images, task_id) -> None:
    from runner.test_graders import run_task_selftest

    assert run_task_selftest(ROOT, task_id, repeats=1) == []


def test_agent_image_reports_the_recorded_cli_version(images) -> None:
    out = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", images["claude_code"]["id"], "claude", "--version"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert out == images["claude_code"]["cli_version"]


def test_agent_network_reaches_only_allowlisted_targets(images) -> None:
    from runner import egress

    toolchain = images["toolchain"]["id"]
    target = "llmreg-test-target"
    outside = "llmreg-test-outside"
    subprocess.run(["docker", "rm", "-f", target], capture_output=True)
    subprocess.run(["docker", "network", "create", outside], capture_output=True)
    try:
        subprocess.run(
            ["docker", "run", "-d", "--name", target, "--network", outside, "--user", "65534", toolchain,
             "python3", "-c", "import http.server as h; h.HTTPServer(('', 8000), h.SimpleHTTPRequestHandler).serve_forever()"],
            capture_output=True, check=True,
        )
        egress.ensure_egress(toolchain, ("api.anthropic.com:443", f"{target}:8000"))
        subprocess.run(["docker", "network", "connect", outside, egress.PROXY_NAME], capture_output=True)
        probe = (
            "import socket\n"
            "def via(t):\n"
            "    s = socket.create_connection(('llmreg-egress', 3128), timeout=5)\n"
            "    s.sendall(f'CONNECT {t} HTTP/1.1\\r\\n\\r\\n'.encode())\n"
            "    return s.recv(200).split(b'\\r\\n')[0].decode()\n"
            "def direct(h, p):\n"
            "    try:\n"
            "        socket.create_connection((h, p), timeout=3); return 'open'\n"
            "    except OSError:\n"
            "        return 'blocked'\n"
            f"print(via('{target}:8000')); print(via('example.com:443'))\n"
            f"print(direct('{target}', 8000)); print(direct('1.1.1.1', 443))\n"
        )
        out = subprocess.run(
            ["docker", "run", "--rm", "--network", egress.NETWORK, "--user", "12345:12345", toolchain,
             "python3", "-c", probe],
            capture_output=True, text=True, timeout=120, check=True,
        ).stdout.splitlines()
        assert out[0].startswith("HTTP/1.1 200")
        assert out[1].startswith("HTTP/1.1 403")
        assert out[2:] == ["blocked", "blocked"]
    finally:
        subprocess.run(["docker", "rm", "-f", target], capture_output=True)
        subprocess.run(["docker", "network", "disconnect", outside, egress.PROXY_NAME], capture_output=True)
        subprocess.run(["docker", "network", "rm", outside], capture_output=True)
        egress.egress_down()


def test_end_to_end_attempt_runs_agent_and_grader_in_containers(images, tmp_path, monkeypatch) -> None:
    from runner import egress
    from runner.artifacts import ArtifactStore
    from runner.coordinator import Coordinator
    from runner.evaluate import _config_for
    from runner.providers.claude_code_cli import ClaudeCodeCLIProvider, ContainerRuntime
    from runner.storage import Store
    from runner.tasks import select_tasks

    # A throwaway agent image: the recorded one with the fake CLI as `claude`.
    with tempfile.TemporaryDirectory() as context:
        (Path(context) / "claude").write_bytes(FAKE.read_bytes())
        (Path(context) / "Dockerfile").write_text(
            f"FROM {images['claude_code']['tag']}\nCOPY --chmod=755 claude /usr/local/bin/claude\n"
        )
        subprocess.run(["docker", "build", "-q", "-t", "llmreg/claude-code:fake-test", context],
                       capture_output=True, check=True)
    task = select_tasks(ROOT / "tasks", ["BUG-PY-01"])[0]
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    route = egress.ensure_egress(images["toolchain"]["id"], egress.allowlist("claude_code_cli"))
    try:
        runtime = ContainerRuntime(
            image="llmreg/claude-code:fake-test", network=route.network, proxy_url=route.proxy_url,
            egress_allow=route.allow,
        )
        provider = ClaudeCodeCLIProvider(
            claude_bin=Path("claude"), model=MODEL, effort="xhigh", runtime=runtime,
            extra_env={"CLAUDE_FAKE_PATCH_TEXT": task.gold_patch.read_text(encoding="utf-8")},
        )
        store = Store(f"sqlite:///{tmp_path / 'reg.db'}")
        coordinator = Coordinator(store, ArtifactStore(tmp_path / "arts"), provider, ROOT)
        suite_id, task_version_id = coordinator.ensure_suite_and_task("canary", task.version, task)
        config = _config_for(track="claude_code_product", model=MODEL, effort="xhigh", client_mode="latest")
        config_id = coordinator.ensure_config(config, config.toolset_version, config.system_prompt_version)
        run_id = store.create_run(
            {"suite_id": suite_id, "config_id": config_id, "trigger": "test", "status": "running",
             "runner_git_sha": "test"}
        )
        outcome = coordinator.run_attempt(
            run_id=run_id, suite_version=task.version, task=task, task_version_id=task_version_id,
            config=config, trial_index=0, source="model", work_root=tmp_path / "work",
        )
    finally:
        egress.egress_down()
        subprocess.run(["docker", "rmi", "-f", "llmreg/claude-code:fake-test"], capture_output=True)
    assert outcome.quality_status == "quality_pass", outcome.error
    assert outcome.grade is not None and outcome.grade.details["backend"] == "docker"
    summary = outcome.provider_result.metadata["summary"]
    assert summary["isolation"] == "container"
    assert summary["runtime"]["egress_allow"] == ["api.anthropic.com:443"]


def test_mined_pytest_task_selftests_inside_the_container(images, tmp_path) -> None:
    from runner.mine import mine
    from runner.sandbox import TaskSandbox
    from runner.tasks import select_tasks
    from runner.test_graders import run_task_selftest

    repo = tmp_path / "app"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                       capture_output=True, check=True)

    def commit(files: dict[str, str], message: str) -> None:
        for rel, content in files.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(content)
        git("add", "-A")
        git("commit", "-q", "-m", message)

    git("init", "-q")
    commit({"calc/__init__.py": "", "calc/ops.py": "def add(a, b):\n    return a - b\n"}, "initial")
    commit({"calc/ops.py": "def add(a, b):\n    return a + b\n",
            "tests/test_add.py": "from calc.ops import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"}, "fix")
    root = tmp_path / "harness"
    (root / "tasks").mkdir(parents=True)
    mine(repo=repo, commit="HEAD", task_id="MINE-CALC-01", root=root,
         functional_command="python3 -m pytest -q -p no:cacheprovider {tests} --junitxml {junit}")
    task = select_tasks(root / "tasks", ["MINE-CALC-01"])[0]
    sandbox = TaskSandbox(task.fixture_path, image=task.manifest["environment"]["image"])
    try:
        assert sandbox.uses_docker()
    finally:
        sandbox.cleanup()
    assert run_task_selftest(root, "MINE-CALC-01", repeats=1) == []


def test_mined_task_with_requirements_grades_in_its_environment(images, tmp_path, monkeypatch) -> None:
    from runner import images as image_module
    from runner.mine import mine
    from runner.sandbox import TaskSandbox
    from runner.tasks import select_tasks
    from runner.test_graders import run_task_selftest

    repo = tmp_path / "app"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                       capture_output=True, check=True)

    git("init", "-q")
    for files, message in [
        ({"calc/__init__.py": "", "calc/ops.py": "def add(a, b):\n    return a - b\n"}, "initial"),
        ({"calc/ops.py": "def add(a, b):\n    return a + b\n",
          "tests/test_add.py": "from calc.ops import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"}, "fix"),
    ]:
        for rel, content in files.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(content)
        git("add", "-A")
        git("commit", "-q", "-m", message)
    root = tmp_path / "harness"
    (root / "tasks").mkdir(parents=True)
    # Already satisfied in the toolchain, so the build needs no network.
    mine(repo=repo, commit="HEAD", task_id="MINE-ENV-01", root=root, requirements=b"pytest==8.3.5\n",
         functional_command="python3 -m pytest -q -p no:cacheprovider {tests} --junitxml {junit}")
    task = select_tasks(root / "tasks", ["MINE-ENV-01"])[0]
    sha = task.manifest["environment"]["requirements"]["sha256"]
    copy = tmp_path / "images.json"
    copy.write_text(json.dumps(images))
    monkeypatch.setenv("LLMREG_IMAGES_FILE", str(copy))
    image_module.build_environments([task], agent=True)
    built = image_module.load_images()["environments"][sha]
    assert built["tasks"] == ["MINE-ENV-01"]
    assert image_module.resolve_environment(sha, "agent") == built["agent"]["id"]
    sandbox = TaskSandbox(task.fixture_path, image=task.manifest["environment"]["image"], requirements_sha256=sha)
    try:
        assert sandbox.resolved_image() == built["grader"]["id"] != images["toolchain"]["id"]
    finally:
        sandbox.cleanup()
    assert run_task_selftest(root, "MINE-ENV-01", repeats=1) == []
    for kind in ("grader", "agent"):
        subprocess.run(["docker", "rmi", "-f", built[kind]["tag"]], capture_output=True)
