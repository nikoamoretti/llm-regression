from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from runner import images
from runner.artifacts import ArtifactStore
from runner.coordinator import Coordinator
from runner.evaluate import _config_for
from runner.mine import MineError, mine, mine_batch
from runner.providers.claude_code_cli import ClaudeCodeCLIProvider, ContainerRuntime
from runner.storage import Store
from runner.tasks import select_tasks

MODEL = "claude-opus-5-5"
PYTEST = "python3 -m pytest -q -p no:cacheprovider {tests} --junitxml {junit}"
REQUIREMENTS = b"pytest==8.3.5\n"
REQUIREMENTS_SHA = hashlib.sha256(REQUIREMENTS).hexdigest()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


@pytest.fixture
def source(tmp_path) -> tuple[Path, str]:
    repo = tmp_path / "repos" / "app"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    for files, message in [
        ({"calc/__init__.py": "", "calc/ops.py": "def add(a, b):\n    return a - b\n"}, "initial"),
        ({"calc/ops.py": "def add(a, b):\n    return a + b\n",
          "tests/test_add.py": "from calc.ops import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"}, "fix"),
    ]:
        for rel, content in files.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(content)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", message)
    return repo, _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def harness(tmp_path, source) -> Path:
    root = tmp_path / "harness"
    (root / "tasks").mkdir(parents=True)
    repo, commit = source
    mine(repo=repo, commit=commit, task_id="MINE-ENV-01", root=root, functional_command=PYTEST,
         requirements=REQUIREMENTS)
    return root


def _record(monkeypatch, tmp_path, data: dict) -> None:
    path = tmp_path / "images.json"
    path.write_text(json.dumps(data))
    monkeypatch.setenv("LLMREG_IMAGES_FILE", str(path))


def _env_record(base_ids: dict[str, str], dockerfile_sha: str | None = None) -> dict:
    sha = dockerfile_sha or hashlib.sha256(images.ENV_DOCKERFILE.read_bytes()).hexdigest()
    return {
        "toolchain": {"id": base_ids["grader"], "tag": "llmreg/toolchain:t"},
        "claude_code": {"id": base_ids["agent"], "tag": "llmreg/claude-code:t"},
        "environments": {
            REQUIREMENTS_SHA: {
                kind: {"id": f"sha256:env-{kind}", "base_id": base_ids[kind], "dockerfile_sha256": sha}
                for kind in ("grader", "agent")
            }
        },
    }


def test_mined_task_pins_its_requirements(harness) -> None:
    task = select_tasks(harness / "tasks", ["MINE-ENV-01"])[0]
    spec = task.manifest["environment"]["requirements"]
    assert spec == {"path": "environment/requirements.txt", "sha256": REQUIREMENTS_SHA}
    assert (task.root / spec["path"]).read_bytes() == REQUIREMENTS
    assert images.task_requirements(task.manifest, task.root)[1] == REQUIREMENTS_SHA


def test_edited_or_escaping_requirements_are_rejected(harness) -> None:
    task = select_tasks(harness / "tasks", ["MINE-ENV-01"])[0]
    (task.root / "environment" / "requirements.txt").write_bytes(b"pytest==9.0.0\n")
    with pytest.raises(ValueError, match="does not match"):
        images.task_requirements(task.manifest, task.root)
    escaping = {"id": "X", "environment": {"requirements": {"path": "../../etc/passwd", "sha256": "0"}}}
    with pytest.raises(ValueError, match="escapes"):
        images.task_requirements(escaping, task.root)
    assert images.task_requirements({"environment": {}}, task.root) is None


def test_environment_resolves_only_on_its_current_bases(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(images, "docker_image_available", lambda ref: True)
    bases = {"grader": "sha256:tool", "agent": "sha256:cc"}
    _record(monkeypatch, tmp_path, _env_record(bases))
    assert images.resolve_environment(REQUIREMENTS_SHA, "grader") == "sha256:env-grader"
    assert images.resolve_environment(REQUIREMENTS_SHA, "agent") == "sha256:env-agent"
    assert images.resolve_task_image("sol-regression-python@sha256:local", REQUIREMENTS_SHA) == "sha256:env-grader"

    rebuilt = _env_record(bases)
    rebuilt["toolchain"]["id"] = "sha256:newer-tool"
    _record(monkeypatch, tmp_path, rebuilt)
    assert images.resolve_environment(REQUIREMENTS_SHA, "grader") is None
    assert images.resolve_environment(REQUIREMENTS_SHA, "agent") == "sha256:env-agent"

    _record(monkeypatch, tmp_path, _env_record(bases, dockerfile_sha="0" * 64))
    assert images.resolve_environment(REQUIREMENTS_SHA, "agent") is None


def test_a_task_with_requirements_never_falls_back_to_the_bare_toolchain(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(images, "docker_image_available", lambda ref: True)
    _record(monkeypatch, tmp_path, {"toolchain": {"id": "sha256:tool"}})
    assert images.resolve_task_image("sol-regression-python@sha256:local") == "sha256:tool"
    assert images.resolve_task_image("sol-regression-python@sha256:local", REQUIREMENTS_SHA) is None


class _ContainerProvider:
    name = "claude_code_cli"
    track = "claude_code_product"
    runtime = ContainerRuntime(image="sha256:cc", network="n", proxy_url="http://p:3128")

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run_attempt(self, **kwargs: object):
        self.calls.append(kwargs)
        raise AssertionError("the agent must not run without its environment")


def _attempt(root: Path, tmp_path: Path, provider) -> object:
    task = select_tasks(root / "tasks", ["MINE-ENV-01"])[0]
    store = Store(f"sqlite:///{tmp_path / 'reg.db'}")
    coordinator = Coordinator(store, ArtifactStore(tmp_path / "arts"), provider, root)
    suite_id, task_version_id = coordinator.ensure_suite_and_task("mined", task.version, task)
    config = _config_for(track="claude_code_product", model=MODEL, effort="xhigh", client_mode="latest")
    config_id = coordinator.ensure_config(config, config.toolset_version, config.system_prompt_version)
    run_id = store.create_run({"suite_id": suite_id, "config_id": config_id, "trigger": "test",
                               "status": "running", "runner_git_sha": "test"})
    return coordinator.run_attempt(run_id=run_id, suite_version=task.version, task=task,
                                   task_version_id=task_version_id, config=config, trial_index=0,
                                   source="model", work_root=tmp_path / "work")


def test_missing_environment_fails_before_the_agent_runs(harness, tmp_path, monkeypatch) -> None:
    provider = _ContainerProvider()
    outcome = _attempt(harness, tmp_path / "a", provider)
    assert outcome.quality_status == "invalid_configuration"
    assert "images build-env" in outcome.error["message"]
    # Grading on the host is allowed, but the containerized agent still needs its environment.
    monkeypatch.setenv("LLMREG_ALLOW_HOST", "1")
    outcome = _attempt(harness, tmp_path / "b", provider)
    assert outcome.quality_status == "invalid_configuration"
    assert "no agent environment image" in outcome.error["message"]
    assert provider.calls == []


def test_edited_requirements_are_a_task_mutation(harness, tmp_path) -> None:
    task_root = harness / "tasks" / "MINE-ENV-01" / "v1"
    (task_root / "environment" / "requirements.txt").write_bytes(b"requests\n")
    outcome = _attempt(harness, tmp_path, _ContainerProvider())
    assert outcome.quality_status == "invalid_configuration"
    assert "task mutation" in outcome.error["message"]


def test_provider_runs_the_agent_in_the_task_environment(tmp_path, monkeypatch) -> None:
    import runner.providers.claude_code_cli as cli

    seen: list[list[str]] = []

    def fake_run(command, **_):
        seen.append(list(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    runtime = ContainerRuntime(image="sha256:cc", network="n", proxy_url="http://p:3128")
    provider = ClaudeCodeCLIProvider(claude_bin=Path("claude"), model=MODEL, effort="high", runtime=runtime)
    result = provider.run_attempt(prompt="p", workspace=tmp_path, model=MODEL, effort="high", timeout_seconds=5,
                                  image="sha256:env-agent")
    assert "sha256:env-agent" in seen[0] and "sha256:cc" not in seen[0]
    assert result.metadata["summary"]["runtime"]["image"] == "sha256:env-agent"
    assert provider.runtime.image == "sha256:cc"  # the shared runtime is not mutated


def _recipes(tmp_path: Path, repo: Path, commit: str) -> Path:
    configs = tmp_path / "configs"
    (configs / "mined").mkdir(parents=True)
    (configs / "mined" / "MINE-ENV-02.md").write_text("add() returns the wrong value.\n")
    (configs / "mined" / "requirements.txt").write_bytes(REQUIREMENTS)
    recipes = configs / "mined.yaml"
    recipes.write_text(yaml.safe_dump({"tasks": [{
        "id": "MINE-ENV-02", "repo": repo.name, "commit": commit, "prompt": "mined/MINE-ENV-02.md",
        "functional_command": PYTEST, "requirements": "mined/requirements.txt",
    }]}))
    return recipes


def test_batch_mining_is_reproducible_and_checked_against_the_lock(tmp_path, source) -> None:
    repo, commit = source
    recipes = _recipes(tmp_path, repo, commit)
    root = tmp_path / "harness"
    (root / "tasks").mkdir(parents=True)
    first = mine_batch(recipes, repos_dir=repo.parent, root=root)
    assert first == [{"id": "MINE-ENV-02", "mined": True, "recorded": True}]
    lock = json.loads(recipes.with_suffix(".lock.json").read_text())
    assert lock["MINE-ENV-02"]["requirements_sha256"] == REQUIREMENTS_SHA
    assert select_tasks(root / "tasks", ["MINE-ENV-02"])[0].prompt_text() == "add() returns the wrong value.\n"

    # A fresh checkout materializes the identical task.
    fresh = tmp_path / "fresh"
    (fresh / "tasks").mkdir(parents=True)
    assert mine_batch(recipes, repos_dir=repo.parent, root=fresh) == [{"id": "MINE-ENV-02", "mined": True}]

    # An edited recipe prompt is not silently ignored by an existing materialization.
    (recipes.parent / "mined" / "MINE-ENV-02.md").write_text("edited\n")
    with pytest.raises(MineError, match="materialized prompt differs"):
        mine_batch(recipes, repos_dir=repo.parent, root=fresh)

    # A different task under the same id is refused unless re-recorded.
    (recipes.parent / "mined" / "MINE-ENV-02.md").write_text("changed\n")
    other = tmp_path / "other"
    (other / "tasks").mkdir(parents=True)
    with pytest.raises(MineError, match="prompt_sha256"):
        mine_batch(recipes, repos_dir=repo.parent, root=other)
    assert mine_batch(recipes, repos_dir=repo.parent, root=other, record=True)[0]["recorded"]


def test_batch_rejects_unknown_recipe_keys_and_missing_clones(tmp_path, source) -> None:
    repo, commit = source
    recipes = _recipes(tmp_path, repo, commit)
    data = yaml.safe_load(recipes.read_text())
    root = tmp_path / "harness"
    (root / "tasks").mkdir(parents=True)
    with pytest.raises(MineError, match="no git clone"):
        mine_batch(recipes, repos_dir=tmp_path / "nowhere", root=root)
    data["tasks"][0]["comit"] = commit
    recipes.write_text(yaml.safe_dump(data))
    with pytest.raises(MineError, match="unknown recipe keys"):
        mine_batch(recipes, repos_dir=repo.parent, root=root)


def test_composite_task_spans_a_commit_range(tmp_path) -> None:
    repo = tmp_path / "repos" / "chain"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    steps = [
        ({"calc/__init__.py": "", "calc/ops.py": "def add(a, b):\n    return a - b\n"}, "initial"),
        ({"calc/ops.py": "def add(a, b):\n    return a + b\n",
          "tests/test_add.py": "from calc.ops import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
          "web/e2e/add.spec.ts": "test('adds', () => {});\n"}, "fix add"),
        ({"calc/ops.py": "def add(a, b):\n    return a + b\n\n\ndef mul(a, b):\n    return a * b\n",
          "tests/test_mul.py": "from calc.ops import mul\n\n\ndef test_mul():\n    assert mul(2, 3) == 6\n"}, "add mul"),
    ]
    shas = []
    for files, message in steps:
        for rel, content in files.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(content)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", message)
        shas.append(_git(repo, "rev-parse", "HEAD"))
    root = tmp_path / "harness"
    (root / "tasks").mkdir(parents=True)
    mine(repo=repo, commit=shas[2], base=shas[0], task_id="MINE-CHAIN-01", root=root,
         functional_command=PYTEST, prompt_text="add() is wrong and mul() is missing.\n")
    task = select_tasks(root / "tasks", ["MINE-CHAIN-01"])[0]
    source = task.manifest["source"]
    assert source["parent"] == shas[0] and source["commits"] == shas[1:]
    # The browser spec cannot run under pytest, so it is neither a hidden test nor part of the fix.
    assert source["hidden_tests"] == ["tests/test_add.py", "tests/test_mul.py"]
    assert "web/e2e/add.spec.ts" not in source["fix_paths"]
    gold = (root / "private_graders" / "MINE-CHAIN-01" / "v1" / "gold.patch").read_text()
    assert "a + b" in gold and "def mul" in gold
    assert "return a - b" in (task.fixture_path / "calc" / "ops.py").read_text()
    with pytest.raises(MineError, match="not an ancestor"):
        mine(repo=repo, commit=shas[0], base=shas[2], task_id="MINE-CHAIN-02", root=root,
             functional_command=PYTEST, prompt_text="x\n")
