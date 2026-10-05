#!/usr/bin/env python3
"""Generic grader for mined tasks (copied into each task's private grader).

Reads ``grader_config.json`` beside this file, then, in the finished workspace:

1. restores test configuration to its pre-fix state (``protected/``) and
   deletes test-config files the agent created (e.g. a new ``conftest.py``
   that skips tests), so the agent cannot change how tests run;
2. copies the hidden tests (the fix commit's versions) over the workspace;
3. runs the functional command (hidden tests) and, if configured, the
   regression command (the visible suite);
4. prints one JSON object in the harness grader format.

With a ``{junit}`` placeholder the command writes JUnit XML and each testcase
counts; skipped testcases count as failures. Without it a group is a single
pass/fail on the exit code. Standard library only.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKSPACE = Path(os.environ.get("WORKSPACE", "/workspace"))
PROTECTED_NAMES = {
    "conftest.py",
    "pytest.ini",
    "tox.ini",
    ".mocharc.json",
    ".mocharc.js",
    ".mocharc.yml",
    "jest.config.js",
    "jest.config.ts",
    "jest.config.mjs",
    "vitest.config.js",
    "vitest.config.ts",
    "vitest.config.mjs",
}


def restore_protected(config: dict) -> list[str]:
    actions = []
    allowed = set(config.get("protected") or [])
    for path in list(WORKSPACE.rglob("*")):
        rel = path.relative_to(WORKSPACE).as_posix()
        if ".git" in path.parts or "node_modules" in path.parts:
            continue
        if path.is_file() and path.name in PROTECTED_NAMES and rel not in allowed:
            path.unlink()
            actions.append(f"removed {rel}")
    for rel in sorted(allowed):
        source = ROOT / "protected" / rel
        target = WORKSPACE / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return actions


def install_hidden_tests(config: dict) -> None:
    for rel in config["hidden_tests"]:
        target = WORKSPACE / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "hidden_tests" / rel, target)


def parse_junit(path: Path) -> list[dict]:
    tests = []
    root = ET.parse(path).getroot()
    for case in root.iter("testcase"):
        name = ".".join(part for part in (case.get("classname"), case.get("name")) if part)
        failed = case.find("failure") is not None or case.find("error") is not None
        skipped = case.find("skipped") is not None
        detail = None
        for tag in ("failure", "error", "skipped"):
            node = case.find(tag)
            if node is not None:
                detail = (tag + ": " + (node.get("message") or node.text or "")).strip()[:400]
                break
        tests.append({"id": name, "passed": not (failed or skipped), "failure_signature": detail})
    return tests


def run_group(name: str, template: str | None, tests_arg: list[str], timeout: int) -> dict:
    if not template:
        return {"passed": 0, "total": 0, "tests": [], "log": ""}
    with tempfile.TemporaryDirectory(prefix=f"grade-{name}-") as tmp:
        junit = Path(tmp) / "junit.xml"
        argv = []
        for part in shlex.split(template):
            if part == "{tests}":
                argv.extend(tests_arg)
            else:
                argv.append(part.replace("{junit}", str(junit)))
        try:
            proc = subprocess.run(argv, cwd=WORKSPACE, capture_output=True, text=True, timeout=timeout, check=False)
            log, code = (proc.stdout or "") + (proc.stderr or ""), proc.returncode
        except subprocess.TimeoutExpired:
            log, code = f"{name} command timed out after {timeout}s", 124
        except OSError as exc:
            log, code = f"{name} command failed to start: {exc}", 127
        tests = []
        if "{junit}" in template and junit.exists():
            try:
                tests = parse_junit(junit)
            except ET.ParseError as exc:
                log += f"\nunreadable JUnit XML: {exc}"
        if tests:
            if code != 0 and all(item["passed"] for item in tests):
                # The process failed outside any testcase (collection error, crash).
                tests.append({"id": f"{name}:process", "passed": False, "failure_signature": f"exit {code}"})
        else:
            tests = [{"id": f"{name}:command", "passed": code == 0, "failure_signature": None if code == 0 else f"exit {code}"}]
    for item in tests:
        item["group"] = name
    passed = sum(1 for item in tests if item["passed"])
    return {"passed": passed, "total": len(tests), "tests": tests, "log": log[-4000:]}


def main() -> int:
    config = json.loads((ROOT / "grader_config.json").read_text(encoding="utf-8"))
    actions = restore_protected(config)
    install_hidden_tests(config)
    timeout = int(config.get("timeout_seconds") or 600)
    functional = run_group("functional", config["functional_command"], config["hidden_tests"], timeout)
    regression = run_group("regression", config.get("regression_command"), [], timeout)
    constraints = {"passed": 1, "total": 1, "tests": [{"id": "test-config-restored", "group": "constraints", "passed": True}]}
    payload = {
        "functional": {"passed": functional["passed"], "total": functional["total"]},
        "regression": {"passed": regression["passed"], "total": regression["total"]},
        "constraints": {"passed": constraints["passed"], "total": constraints["total"]},
        "forbidden_changes": [],
        "strict_pass": functional["total"] > 0
        and functional["passed"] == functional["total"]
        and regression["passed"] == regression["total"],
        "tests": functional["tests"] + regression["tests"] + constraints["tests"],
        "logs": {"functional": functional["log"], "regression": regression["log"], "restored": actions},
    }
    print(json.dumps(payload))
    return 0 if payload["strict_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
