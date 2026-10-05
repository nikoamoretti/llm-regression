from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from runner import mine_grader
from runner.mine import MineError, is_test_path, mine
from runner.tasks import select_tasks, validate_task
from runner.test_graders import run_task_selftest

UNITTEST = "python3 -m unittest {tests}"
PYTEST = "python3 -m pytest -q -p no:cacheprovider {tests} --junitxml {junit}"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _commit(repo: Path, files: dict[str, str], message: str) -> str:
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path) -> tuple[Path, str]:
    repo = tmp_path / "app"
    repo.mkdir()
    _git(repo, "init", "-q")
    _commit(
        repo,
        {
            "calc/__init__.py": "",
            "calc/ops.py": "def add(a, b):\n    return a - b\n",
            "tests/test_import.py": "import unittest\nimport calc.ops\n\n\nclass T(unittest.TestCase):\n"
            "    def test_import(self):\n        self.assertTrue(callable(calc.ops.add))\n",
            "conftest.py": "# project conftest\n",
        },
        "initial",
    )
    fix = _commit(
        repo,
        {
            "calc/ops.py": "def add(a, b):\n    return a + b\n",
            "tests/test_add.py": "import unittest\nfrom calc.ops import add\n\n\nclass T(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n",
        },
        "Fix add returning the difference\n\nadd(2, 3) returned -1.",
    )
    return repo, fix


@pytest.fixture
def root(tmp_path) -> Path:
    root = tmp_path / "harness"
    (root / "tasks").mkdir(parents=True)
    (root / "private_graders").mkdir()
    return root


def test_test_path_detection() -> None:
    assert is_test_path("tests/test_add.py") and is_test_path("pkg/foo_test.go")
    assert is_test_path("web/src/__tests__/a.ts") and is_test_path("src/a.spec.ts")
    assert not is_test_path("src/attestation.py") and not is_test_path("calc/ops.py")


def test_mined_task_is_valid_and_hides_the_fix_and_its_tests(repo, root) -> None:
    path, fix = repo
    task_dir = mine(repo=path, commit=fix, task_id="MINE-CALC-01", functional_command=UNITTEST, root=root)
    fixture = task_dir / "fixture"
    assert (fixture / "calc/ops.py").read_text() == "def add(a, b):\n    return a - b\n"
    assert not (fixture / "tests/test_add.py").exists()
    grader = root / "private_graders/MINE-CALC-01/v1"
    assert "assertEqual(add(2, 3), 5)" in (grader / "hidden_tests/tests/test_add.py").read_text()
    gold = (grader / "gold.patch").read_text()
    assert "calc/ops.py" in gold and "test_add" not in gold
    config = json.loads((grader / "grader_config.json").read_text())
    assert config["hidden_tests"] == ["tests/test_add.py"] and config["protected"] == ["conftest.py"]
    manifest = yaml.safe_load((task_dir / "task.yaml").read_text())
    assert manifest["language"] == "python" and manifest["environment"]["image"].startswith("sol-regression-python")
    assert manifest["task_review_status"] == "pending_human_review"
    assert manifest["source"]["commit"] == fix and manifest["source"]["fix_paths"] == ["calc/ops.py"]
    assert "Fix add returning the difference" in (task_dir / "prompt.md").read_text()
    task = select_tasks(root / "tasks", ["MINE-CALC-01"])[0]
    assert validate_task(task, check_hashes=True) == []


@pytest.mark.parametrize("command", [UNITTEST, PYTEST])
def test_mined_task_passes_the_grader_selftest(repo, root, command) -> None:
    path, fix = repo
    mine(repo=path, commit=fix, task_id="MINE-CALC-01", functional_command=command, root=root)
    assert run_task_selftest(root, "MINE-CALC-01", repeats=1) == []


def test_a_conftest_that_skips_the_hidden_tests_does_not_pass(repo, root) -> None:
    path, fix = repo
    mine(repo=path, commit=fix, task_id="MINE-CALC-01", functional_command=PYTEST, root=root)
    grader = root / "private_graders/MINE-CALC-01/v1"
    skip_all = (
        "diff --git a/tests/conftest.py b/tests/conftest.py\nnew file mode 100644\n--- /dev/null\n"
        "+++ b/tests/conftest.py\n@@ -0,0 +1,4 @@\n+import pytest\n+\n"
        "+def pytest_collection_modifyitems(items):\n+    [i.add_marker(pytest.mark.skip) for i in items]\n"
    )
    (grader / "negatives" / "skip_all.patch").write_text(skip_all)
    # The self-test requires every negative to fail; the planted conftest is removed before grading.
    assert run_task_selftest(root, "MINE-CALC-01", repeats=1) == []


def test_bad_commits_are_refused_without_partial_output(repo, root) -> None:
    path, fix = repo
    tests_only = _commit(path, {"tests/test_more.py": "import unittest\n"}, "tests only")
    with pytest.raises(MineError, match="only tests"):
        mine(repo=path, commit=tests_only, task_id="MINE-X-01", functional_command=UNITTEST, root=root)
    no_tests = _commit(path, {"calc/ops.py": "def add(a, b):\n    return b + a\n"}, "refactor")
    with pytest.raises(MineError, match="no test files"):
        mine(repo=path, commit=no_tests, task_id="MINE-X-01", functional_command=UNITTEST, root=root)
    with pytest.raises(MineError, match=r"\{tests\}"):
        mine(repo=path, commit=fix, task_id="MINE-X-01", functional_command="python3 -m unittest", root=root)
    assert not (root / "tasks" / "MINE-X-01").exists()
    assert not (root / "private_graders" / "MINE-X-01").exists()
    mine(repo=path, commit=fix, task_id="MINE-X-01", functional_command=UNITTEST, root=root)
    with pytest.raises(MineError, match="already exists"):
        mine(repo=path, commit=fix, task_id="MINE-X-01", functional_command=UNITTEST, root=root)


def test_junit_counts_skips_as_failures(tmp_path) -> None:
    report = tmp_path / "junit.xml"
    report.write_text(
        '<testsuite><testcase classname="t" name="ok"/>'
        '<testcase classname="t" name="bad"><failure message="boom"/></testcase>'
        '<testcase classname="t" name="skipped"><skipped message="nope"/></testcase></testsuite>'
    )
    results = {item["id"]: item["passed"] for item in mine_grader.parse_junit(report)}
    assert results == {"t.ok": True, "t.bad": False, "t.skipped": False}
