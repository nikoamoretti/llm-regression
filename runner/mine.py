"""Mine a frozen task from a real bug-fix commit in a local git repository.

The task is the repository just before the fix (the fixture), a prompt, and the
fix commit's own tests as hidden tests. The fix itself becomes the gold patch,
and an empty patch is the negative. A mined task is valid only when its hidden
tests fail on the fixture and pass with the gold patch; ``runner mine`` checks
that with the normal grader self-test before you add the task to a suite.

Private repositories make realistic tasks the model cannot have seen. Prompts
drafted from commit messages often describe the fix: rewrite them as an issue
before human review (``task_review_status`` stays ``pending_human_review``).
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from runner.hash_tree import hash_file, hash_tree
from runner.mine_grader import PROTECTED_NAMES

ROOT = Path(__file__).resolve().parents[1]
GRADER_SOURCE = Path(__file__).resolve().with_name("mine_grader.py")
TEST_FILE_PATTERNS = (
    "test_*.py",
    "*_test.py",
    "*_test.go",
    "*.test.js",
    "*.test.jsx",
    "*.test.ts",
    "*.test.tsx",
    "*.test.mjs",
    "*.test.cjs",
    "*.spec.js",
    "*.spec.jsx",
    "*.spec.ts",
    "*.spec.tsx",
)
TEST_DIRS = {"tests", "test", "__tests__", "spec", "testdata"}
LANGUAGE_IMAGES = {
    "python": "sol-regression-python@sha256:local",
    "typescript": "sol-regression-node@sha256:local",
    "javascript": "sol-regression-node@sha256:local",
    "go": "sol-regression-go@sha256:local",
    "rust": "sol-regression-rust@sha256:local",
}
EXTENSION_LANGUAGES = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".go": "go",
    ".rs": "rust",
}


class MineError(RuntimeError):
    pass


def is_test_path(path: str) -> bool:
    pure = PurePosixPath(path)
    if any(fnmatch.fnmatch(pure.name, pattern) for pattern in TEST_FILE_PATTERNS):
        return True
    return any(part in TEST_DIRS for part in pure.parts[:-1])


def _git(repo: Path, *args: str, binary: bool = False) -> Any:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    if proc.returncode != 0:
        raise MineError(f"git {' '.join(args)} failed: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout if binary else proc.stdout.decode()


def changed_paths(repo: Path, parent: str, commit: str) -> list[tuple[str, str]]:
    """(status, path) for every path the commit touched; renames list both sides."""
    out = _git(repo, "diff", "--name-status", "-z", "--no-renames", parent, commit)
    fields = [item for item in out.split("\0") if item]
    return [(fields[index], fields[index + 1]) for index in range(0, len(fields) - 1, 2)]


def infer_language(paths: list[str]) -> str:
    counts: dict[str, int] = {}
    for path in paths:
        language = EXTENSION_LANGUAGES.get(PurePosixPath(path).suffix)
        if language:
            counts[language] = counts.get(language, 0) + 1
    if not counts:
        raise MineError("cannot infer the task language from the fix; pass --language")
    return max(sorted(counts), key=lambda item: counts[item])


def _extract_tree(repo: Path, commit: str, dest: Path, max_bytes: int) -> int:
    archive = _git(repo, "archive", "--format=tar", commit, binary=True)
    total = 0
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar.getmembers():
            total += member.size
            if total > max_bytes:
                raise MineError(f"fixture exceeds {max_bytes // 1_000_000} MB; pass --max-fixture-mb to allow it")
        tar.extractall(dest, filter="data")
    return total


def draft_prompt(subject: str, body: str) -> str:
    lines = [
        "<!-- Drafted from the commit message. Rewrite it as an issue report (symptoms, expected",
        "behaviour) without describing the fix, then mark the task reviewed. -->",
        "",
        subject.strip(),
    ]
    if body.strip():
        lines += ["", body.strip()]
    lines += ["", "Make the smallest change that fixes this. Keep existing behaviour and tests passing."]
    return "\n".join(lines) + "\n"


def uv_export(repo: Path, commit: str) -> bytes:
    """Hash-locked requirements from the commit's ``uv.lock`` (all extras, project itself excluded)."""
    with tempfile.TemporaryDirectory(prefix="llmreg-uv-") as tmp:
        for name in ("pyproject.toml", "uv.lock"):
            try:
                (Path(tmp) / name).write_bytes(_git(repo, "show", f"{commit}:{name}", binary=True))
            except MineError as exc:
                raise MineError(f"--uv-export needs {name} at the repository root of {commit[:12]}") from exc
        proc = subprocess.run(
            ["uv", "export", "--frozen", "--all-extras", "--no-emit-project", "--no-header",
             "--format", "requirements-txt"],
            cwd=tmp, capture_output=True, check=False,
        )
    if proc.returncode != 0:
        raise MineError(f"uv export failed: {proc.stderr.decode(errors='replace').strip()[:800]}")
    if b"-e " in proc.stdout or b"file://" in proc.stdout:
        raise MineError("uv export emitted local/editable packages (workspace members?); pass --requirements instead")
    return proc.stdout


def mine(
    *,
    repo: Path,
    commit: str,
    task_id: str,
    functional_command: str,
    regression_command: str | None = None,
    tests: list[str] | None = None,
    prompt_text: str | None = None,
    language: str | None = None,
    family: str = "bugfix",
    difficulty: str = "medium",
    root: Path = ROOT,
    timeout_seconds: int = 600,
    max_fixture_mb: int = 50,
    requirements: bytes | None = None,
    review_notes: str | None = None,
    base: str | None = None,
) -> Path:
    """``requirements``: pinned packages the tests need (see runner/images.py, ``build-env``)."""
    repo = repo.resolve()
    if "{tests}" not in functional_command:
        raise MineError("--functional-command must contain {tests} (the hidden test paths)")
    sha = _git(repo, "rev-parse", "--verify", f"{commit}^{{commit}}").strip()
    if base:
        # A composite task: the fixture is ``base``, the fix is every commit up to ``commit``.
        parent = _git(repo, "rev-parse", "--verify", f"{base}^{{commit}}").strip()
        try:
            _git(repo, "merge-base", "--is-ancestor", parent, sha)
        except MineError as exc:
            raise MineError(f"base {base} is not an ancestor of {commit}") from exc
    else:
        parent = _git(repo, "rev-parse", "--verify", f"{sha}^1").strip()
    changes = changed_paths(repo, parent, sha)
    candidates = [path for status, path in changes if status != "D" and is_test_path(path)]
    source = sorted({path for _, path in changes if path not in set(tests or candidates) and not is_test_path(path)})
    if not source:
        raise MineError("the commit changes only tests; there is no fix to mine")
    language = language or infer_language(source)
    if tests:
        hidden = sorted(tests)
    else:
        # One test runner per task: tests in another language (e.g. browser specs next to
        # Python fixes) cannot run under the functional command, so they are not hidden tests.
        family = {"typescript": "js", "javascript": "js"}.get(language, language)
        hidden = sorted(
            path for path in candidates
            if {"typescript": "js", "javascript": "js"}.get(
                EXTENSION_LANGUAGES.get(PurePosixPath(path).suffix, ""), EXTENSION_LANGUAGES.get(PurePosixPath(path).suffix)
            ) == family
        )
    if not hidden:
        raise MineError("the commit changes no test files; pass --tests with the tests that check the fix")
    if language not in LANGUAGE_IMAGES:
        raise MineError(f"unsupported language {language!r}; known: {', '.join(sorted(LANGUAGE_IMAGES))}")

    task_dir = root / "tasks" / task_id / "v1"
    grader_dir = root / "private_graders" / task_id / "v1"
    for path in (task_dir, grader_dir):
        if path.exists():
            raise MineError(f"{path} already exists; pick another --id")
    fixture = task_dir / "fixture"
    try:
        fixture.mkdir(parents=True)
        _extract_tree(repo, parent, fixture, max_fixture_mb * 1_000_000)

        (grader_dir / "negatives").mkdir(parents=True)
        (grader_dir / "negatives" / "noop.patch").write_text("", encoding="utf-8")
        gold = _git(repo, "diff", "--binary", "--full-index", parent, sha, "--", *source, binary=True)
        if not gold.strip():
            raise MineError("the fix produced an empty gold patch")
        (grader_dir / "gold.patch").write_bytes(gold)
        for rel in hidden:
            target = grader_dir / "hidden_tests" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(_git(repo, "show", f"{sha}:{rel}", binary=True))
        protected = sorted(
            path.relative_to(fixture).as_posix()
            for path in fixture.rglob("*")
            if path.is_file() and path.name in PROTECTED_NAMES and "node_modules" not in path.parts
        )
        for rel in protected:
            target = grader_dir / "protected" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(fixture / rel, target)
        shutil.copyfile(GRADER_SOURCE, grader_dir / "grade.py")
        (grader_dir / "run.sh").write_text(
            '#!/usr/bin/env bash\nset -euo pipefail\ncd "${WORKSPACE:-/workspace}"\npython3 "${GRADER:-/grader}/grade.py"\n',
            encoding="utf-8",
        )
        (grader_dir / "grader_config.json").write_text(
            json.dumps(
                {
                    "functional_command": functional_command,
                    "regression_command": regression_command,
                    "hidden_tests": hidden,
                    "protected": protected,
                    "timeout_seconds": timeout_seconds,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        if requirements:
            (task_dir / "environment").mkdir()
            (task_dir / "environment" / "requirements.txt").write_bytes(requirements)

        subject = _git(repo, "log", "-1", "--format=%s", sha)
        body = _git(repo, "log", "-1", "--format=%b", sha)
        prompt = task_dir / "prompt.md"
        prompt.write_text(prompt_text if prompt_text is not None else draft_prompt(subject, body), encoding="utf-8")

        try:
            remote = _git(repo, "remote", "get-url", "origin").strip()
        except MineError:
            remote = None
        manifest = {
            "schema_version": 1,
            "id": task_id,
            "version": "v1",
            "category": family,
            "language": language,
            "difficulty": difficulty,
            "context_class": "repository",
            "prompt_file": "prompt.md",
            "fixture": {"path": "fixture", "sha256": hash_tree(fixture)},
            "environment": {
                "image": LANGUAGE_IMAGES[language],
                "network": False,
                "env": {"TZ": "UTC", "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8", "PYTHONHASHSEED": "410728"},
                **(
                    {"requirements": {"path": "environment/requirements.txt",
                                      "sha256": hashlib.sha256(requirements).hexdigest()}}
                    if requirements else {}
                ),
            },
            "grader": {
                "command": ["bash", "/grader/run.sh"],
                "timeout_seconds": timeout_seconds + 60,
                "sha256": hash_tree(grader_dir),
            },
            "budgets": {
                "task_wall_seconds": 1800,
                "max_api_calls": 60,
                "max_tool_calls": 200,
                "max_command_calls": 60,
                "max_output_tokens_per_call": 16384,
                "max_cost_usd": 10.0,
            },
            "permissions": {"forbidden_paths": ["grader/", "task.yaml"]},
            "scoring": {
                "strict_pass_requires": ["functional", "regression", "constraints"],
                "groups": {
                    "functional": {"weight": 0.7},
                    "regression": {"weight": 0.2},
                    "constraints": {"weight": 0.1},
                },
            },
            "tags": ["mined"],
            "prompt_sha256": hash_file(prompt),
            "family": family,
            "languages": [language],
            "task_review_status": "pending_human_review",
            "task_reviewed_at": None,
            "task_review_notes": review_notes or "Mined from a fix commit. Review the prompt (it must not describe "
            "the fix) and whether the hidden tests accept every reasonable fix.",
            "grading_protocol": "hidden_final_state",
            "public_test_policy": "fixture_visible_only",
            "network_policy": "disabled",
            "prohibited_modifications": ["grader/", "task.yaml"],
            "private_grader_path": f"private_graders/{task_id}/v1",
            "source": {
                "repository": remote or repo.name,
                "commit": sha,
                "parent": parent,
                **({"commits": _git(repo, "rev-list", "--reverse", f"{parent}..{sha}").split()} if base else {}),
                "mined_at": datetime.now(timezone.utc).isoformat(),
                "hidden_tests": hidden,
                "fix_paths": source,
            },
        }
        (task_dir / "task.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8")
    except BaseException:
        # Leave nothing half-written; other versions of the same id stay untouched.
        for path in (task_dir, grader_dir):
            shutil.rmtree(path, ignore_errors=True)
            if path.parent.exists() and not any(path.parent.iterdir()):
                path.parent.rmdir()
        raise
    return task_dir


RECIPE_KEYS = {
    "id", "repo", "commit", "prompt", "functional_command", "regression_command", "tests", "requirements",
    "language", "family", "difficulty", "timeout_seconds", "max_fixture_mb", "review_notes", "base",
}


def task_hashes(root: Path, task_id: str) -> dict[str, str]:
    """Content hashes that identify a mined task (all deterministic given the recipe)."""
    from runner.tasks import select_tasks

    task = select_tasks(root / "tasks", [task_id])[0]
    hashes = task.computed_hashes()
    spec = (task.manifest.get("environment") or {}).get("requirements")
    if spec:
        hashes["requirements_sha256"] = hash_file(task.root / spec["path"])
    return dict(sorted(hashes.items()))


def mine_batch(
    recipes: Path,
    *,
    repos_dir: Path,
    root: Path = ROOT,
    only: list[str] | None = None,
    record: bool = False,
) -> list[dict[str, Any]]:
    """Materialize every recipe in ``recipes`` and check it against the lock file beside it.

    Recipes (repository, commit, prompt, commands) are committed; the mined
    fixtures and graders are not, because they copy the source repository.
    Mining is deterministic, so the lock's content hashes prove a task
    materialized on another machine is the same frozen task.
    """
    spec = yaml.safe_load(recipes.read_text(encoding="utf-8")) or {}
    lock_path = recipes.with_suffix(".lock.json")
    lock = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.exists() else {}
    results = []
    for recipe in spec.get("tasks") or []:
        task_id = recipe["id"]
        if only and task_id not in only:
            continue
        unknown = set(recipe) - RECIPE_KEYS
        if unknown:
            raise MineError(f"{task_id}: unknown recipe keys {sorted(unknown)}")
        result: dict[str, Any] = {"id": task_id}
        if not (root / "tasks" / task_id / "v1").exists():
            repo = (repos_dir / recipe["repo"]).resolve()
            if not (repo / ".git").exists():
                raise MineError(f"{task_id}: no git clone at {repo} (pass --repos-dir)")
            requirements = recipe.get("requirements")
            if requirements == "uv_export":
                pinned = uv_export(repo, recipe["commit"])
            elif requirements:
                pinned = (recipes.parent / requirements).read_bytes()
            else:
                pinned = None
            mine(
                repo=repo,
                commit=recipe["commit"],
                task_id=task_id,
                functional_command=recipe["functional_command"],
                regression_command=recipe.get("regression_command"),
                tests=recipe.get("tests"),
                prompt_text=(recipes.parent / recipe["prompt"]).read_text(encoding="utf-8"),
                language=recipe.get("language"),
                family=recipe.get("family", "bugfix"),
                difficulty=recipe.get("difficulty", "medium"),
                root=root,
                timeout_seconds=int(recipe.get("timeout_seconds", 600)),
                max_fixture_mb=int(recipe.get("max_fixture_mb", 50)),
                requirements=pinned,
                review_notes=recipe.get("review_notes"),
                base=recipe.get("base"),
            )
            result["mined"] = True
        hashes = task_hashes(root, task_id)
        recipe_prompt = hash_file(recipes.parent / recipe["prompt"])
        if hashes["prompt_sha256"] != recipe_prompt:
            raise MineError(
                f"{task_id}: the materialized prompt differs from {recipe['prompt']}; delete tasks/{task_id} "
                f"and private_graders/{task_id} and re-run (with --record if the change is deliberate)"
            )
        expected = lock.get(task_id)
        if expected and expected != hashes and not record:
            changed = sorted(k for k in set(expected) | set(hashes) if expected.get(k) != hashes.get(k))
            raise MineError(f"{task_id}: materialized task differs from {lock_path.name} ({', '.join(changed)})")
        if record or not expected:
            lock[task_id] = hashes
            result["recorded"] = True
        results.append(result)
    lock_path.write_text(json.dumps(dict(sorted(lock.items())), indent=2) + "\n", encoding="utf-8")
    return results


def batch_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="runner mine --batch", description=mine_batch.__doc__.split("\n\n")[0])
    parser.add_argument("--batch", type=Path, required=True, help="recipe file, e.g. configs/mined.yaml")
    parser.add_argument("--repos-dir", type=Path, required=True, help="directory holding the source clones")
    parser.add_argument("--only", action="append", default=None, help="task id (repeatable)")
    parser.add_argument("--record", action="store_true", help="overwrite the lock with the current hashes")
    parser.add_argument("--build-network", default=None)
    parser.add_argument("--extra-ca", type=Path, default=None)
    parser.add_argument("--no-selftest", action="store_true")
    args = parser.parse_args(argv)
    try:
        results = mine_batch(args.batch, repos_dir=args.repos_dir, only=args.only, record=args.record)
    except MineError as exc:
        print(f"mine: {exc}")
        return 2
    failed = 0
    if not args.no_selftest:
        from runner.images import build_environments
        from runner.tasks import select_tasks
        from runner.test_graders import run_task_selftest

        ids = [r["id"] for r in results]
        build_environments(select_tasks(ROOT / "tasks", ids), agent=False,
                           build_network=args.build_network, extra_ca=args.extra_ca)
        for result in results:
            errors = run_task_selftest(ROOT, result["id"], repeats=1)
            result["selftest"] = "passed" if not errors else "FAILED: " + errors[0][:300]
            failed += bool(errors)
    for result in results:
        flags = " ".join(k for k in ("mined", "recorded") if result.get(k))
        print(f"{result['id']:<16} {flags:<16} {result.get('selftest', '')}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    import sys

    argv = list(sys.argv[1:] if argv is None else argv)
    if any(arg == "--batch" or arg.startswith("--batch=") for arg in argv):
        return batch_main(argv)
    parser = argparse.ArgumentParser(prog="runner mine", description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", type=Path, required=True, help="local git repository")
    parser.add_argument("--commit", required=True, help="the bug-fix commit (the last one, with --base)")
    parser.add_argument("--base", default=None, help="composite task: fixture at BASE, fix = BASE..COMMIT")
    parser.add_argument("--id", dest="task_id", required=True, help="new task id, e.g. MINE-API-01")
    parser.add_argument(
        "--functional-command",
        required=True,
        help='runs the hidden tests; {tests} expands to their paths, optional {junit} to a JUnit XML path, '
        'e.g. "python3 -m pytest -q {tests} --junitxml {junit}"',
    )
    parser.add_argument("--regression-command", default=None, help="optional: the visible test suite")
    parser.add_argument("--tests", nargs="*", default=None, help="hidden test paths (default: test files in the commit)")
    parser.add_argument("--prompt-file", type=Path, default=None)
    parser.add_argument("--language", default=None)
    parser.add_argument("--family", default="bugfix")
    parser.add_argument("--difficulty", default="medium")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--max-fixture-mb", type=int, default=50)
    deps = parser.add_mutually_exclusive_group()
    deps.add_argument("--requirements", type=Path, default=None, help="pinned pip requirements the tests need")
    deps.add_argument("--uv-export", action="store_true", help="pin requirements from the commit's uv.lock")
    parser.add_argument("--build-network", default=None, help="docker build --network for the environment image")
    parser.add_argument("--extra-ca", type=Path, default=None, help="CA bundle for a TLS-inspecting proxy")
    parser.add_argument("--no-selftest", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.uv_export:
            requirements = uv_export(args.repo.resolve(), args.commit)
        elif args.requirements:
            requirements = args.requirements.read_bytes()
        else:
            requirements = None
        task_dir = mine(
            base=args.base,
            requirements=requirements,
            repo=args.repo,
            commit=args.commit,
            task_id=args.task_id,
            functional_command=args.functional_command,
            regression_command=args.regression_command,
            tests=args.tests,
            prompt_text=args.prompt_file.read_text(encoding="utf-8") if args.prompt_file else None,
            language=args.language,
            family=args.family,
            difficulty=args.difficulty,
            timeout_seconds=args.timeout_seconds,
            max_fixture_mb=args.max_fixture_mb,
        )
    except MineError as exc:
        print(f"mine: {exc}")
        return 2
    print(f"wrote {task_dir.relative_to(ROOT)} and private_graders/{args.task_id}/v1")
    if not args.no_selftest:
        from runner.test_graders import run_task_selftest

        if requirements:
            from runner.images import build_environments
            from runner.tasks import select_tasks

            # The self-test grades in the task's environment; build its grader image first.
            build_environments(select_tasks(ROOT / "tasks", [args.task_id]), agent=False,
                               build_network=args.build_network, extra_ca=args.extra_ca)

        errors = run_task_selftest(ROOT, args.task_id, repeats=1)
        if errors:
            print("self-test FAILED (the task is not usable until this passes):")
            for error in errors:
                print(f"  - {error[:400]}")
            return 1
        print("self-test passed: hidden tests fail without the fix and pass with it")
    print(f"next: review tasks/{args.task_id}/v1/prompt.md, then add {args.task_id} to a suite in configs/suites.yaml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
