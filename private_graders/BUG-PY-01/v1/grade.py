#!/usr/bin/env python3
"""Emit machine-readable grader JSON from unittest directories."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKSPACE = Path(os.environ.get("WORKSPACE", "/workspace"))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "src"))


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def run_group(name: str) -> dict:
    start = ROOT / "hidden_tests" / name
    if not start.exists():
        return {"passed": 0, "total": 0, "tests": []}
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    for path in sorted(start.glob("test_*.py")):
        module = load_module(path, f"grader_{name}_{path.stem}")
        suite.addTests(loader.loadTestsFromModule(module))
    stream = StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    tests = []
    bad = {str(case): msg for case, msg in result.failures + result.errors}

    def walk(s):
        for item in s:
            if isinstance(item, unittest.TestSuite):
                walk(item)
            else:
                passed = str(item) not in bad
                tests.append(
                    {
                        "id": getattr(item, "id", lambda: str(item))(),
                        "group": name,
                        "passed": passed,
                        "failure_signature": None if passed else bad.get(str(item), "")[:400],
                    }
                )

    walk(suite)
    passed = result.testsRun - len(result.failures) - len(result.errors)
    return {
        "passed": passed,
        "total": result.testsRun,
        "tests": tests,
        "log": stream.getvalue()[-4000:],
    }


def main() -> int:
    functional = run_group("functional")
    regression = run_group("regression")
    constraints = run_group("constraints")
    tests = functional["tests"] + regression["tests"] + constraints["tests"]
    payload = {
        "functional": {"passed": functional["passed"], "total": functional["total"]},
        "regression": {"passed": regression["passed"], "total": regression["total"]},
        "constraints": {"passed": constraints["passed"], "total": constraints["total"] or 0},
        "forbidden_changes": [],
        "strict_pass": (
            functional["total"] > 0
            and functional["passed"] == functional["total"]
            and regression["passed"] == regression["total"]
            and (constraints["total"] == 0 or constraints["passed"] == constraints["total"])
        ),
        "tests": tests,
        "logs": {
            "functional": functional.get("log"),
            "regression": regression.get("log"),
            "constraints": constraints.get("log"),
        },
    }
    text = json.dumps(payload)
    print(text)
    (WORKSPACE / ".grade.json").write_text(text, encoding="utf-8")
    return 0 if payload["strict_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
