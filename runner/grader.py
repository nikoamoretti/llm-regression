"""Hidden grading: execute task graders and compute strict/partial scores."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from runner.hash_tree import hash_file
from runner.sandbox import SandboxResult, TaskSandbox


@dataclass
class Grade:
    strict_pass: bool
    functional_score: float
    regression_score: float
    constraints_score: float
    partial_score: float
    tests_passed: int
    tests_total: int
    forbidden_change_count: int
    details: dict[str, Any] = field(default_factory=dict)
    test_results: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: float = 0.0
    raw_stdout: str = ""
    raw_stderr: str = ""
    returncode: int = 0


def _ratio(group: dict[str, Any] | None) -> float:
    if not group:
        return 0.0
    total = float(group.get("total") or 0)
    passed = float(group.get("passed") or 0)
    if total <= 0:
        return 1.0
    return max(0.0, min(1.0, passed / total))


def compute_partial(
    functional: float,
    regression: float,
    constraints: float,
    weights: dict[str, float] | None = None,
) -> float:
    weights = weights or {"functional": 0.70, "regression": 0.20, "constraints": 0.10}
    return (
        weights.get("functional", 0.70) * functional
        + weights.get("regression", 0.20) * regression
        + weights.get("constraints", 0.10) * constraints
    )


def host_constraint_check(
    workspace: Path,
    fixture: Path,
    forbidden_paths: list[str],
) -> tuple[list[str], int]:
    violations: list[str] = []
    for rel in forbidden_paths:
        fixture_path = fixture / rel
        work_path = workspace / rel
        if fixture_path.is_file():
            if not work_path.is_file():
                violations.append(f"deleted forbidden file: {rel}")
            elif hash_file(fixture_path) != hash_file(work_path):
                violations.append(f"modified forbidden file: {rel}")
        elif fixture_path.is_dir():
            if work_path.exists():
                # Any file content change under a forbidden directory is a violation.
                for source in fixture_path.rglob("*"):
                    if not source.is_file():
                        continue
                    dest = work_path / source.relative_to(fixture_path)
                    rel_file = dest.relative_to(workspace).as_posix()
                    if not dest.exists() or hash_file(source) != hash_file(dest):
                        violations.append(f"modified forbidden path: {rel_file}")
    return violations, len(violations)


def parse_grade_json(stdout: str, fallback_path: Path | None = None) -> dict[str, Any]:
    candidates: list[str] = []
    if stdout.strip():
        # Prefer the last JSON object in stdout.
        text = stdout.strip()
        start = text.rfind("{")
        if start >= 0:
            candidates.append(text[start:])
        candidates.append(text)
    if fallback_path and fallback_path.exists():
        candidates.append(fallback_path.read_text(encoding="utf-8"))
    for raw in candidates:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and ("functional" in data or "strict_pass" in data):
            return data
    raise ValueError("Grader did not emit machine-readable JSON")


def grade_workspace(
    sandbox: TaskSandbox,
    command: list[str],
    timeout_seconds: int,
    fixture: Path,
    forbidden_paths: list[str],
    weights: dict[str, float] | None = None,
    required: list[str] | None = None,
) -> Grade:
    started = time.perf_counter()
    result: SandboxResult = sandbox.run(command, timeout_seconds=timeout_seconds)
    duration_ms = (time.perf_counter() - started) * 1000
    payload: dict[str, Any] = {}
    parse_error = None
    try:
        payload = parse_grade_json(
            result.stdout,
            sandbox.workspace / ".grade.json",
        )
    except ValueError as exc:
        parse_error = str(exc)

    host_violations, host_count = host_constraint_check(
        sandbox.workspace,
        fixture,
        forbidden_paths,
    )
    forbidden = list(payload.get("forbidden_changes") or []) + host_violations
    forbidden_count = len(forbidden)

    functional = _ratio(payload.get("functional"))
    regression = _ratio(payload.get("regression"))
    constraints = _ratio(payload.get("constraints"))
    if forbidden_count:
        constraints = 0.0

    if parse_error:
        functional = regression = constraints = 0.0

    partial = compute_partial(functional, regression, constraints, weights)
    required = required or ["functional", "regression", "constraints"]
    required_ok = True
    if "functional" in required and functional < 1:
        required_ok = False
    if "regression" in required and regression < 1:
        required_ok = False
    if "constraints" in required and (constraints < 1 or forbidden_count):
        required_ok = False

    tests_passed = int((payload.get("functional") or {}).get("passed") or 0) + int(
        (payload.get("regression") or {}).get("passed") or 0
    )
    tests_total = int((payload.get("functional") or {}).get("total") or 0) + int(
        (payload.get("regression") or {}).get("total") or 0
    )

    strict = bool(payload.get("strict_pass", required_ok)) and required_ok and not parse_error
    if result.returncode not in {0, 1}:
        # 1 is a normal failing test process; other codes are grader crashes.
        if result.returncode != 0 and not payload:
            strict = False

    details = {
        "payload": payload,
        "parse_error": parse_error,
        "forbidden_changes": forbidden,
        "backend": result.backend,
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-2000:],
        "stderr_tail": result.stderr[-2000:],
    }
    return Grade(
        strict_pass=strict,
        functional_score=functional,
        regression_score=regression,
        constraints_score=constraints,
        partial_score=partial,
        tests_passed=tests_passed,
        tests_total=tests_total,
        forbidden_change_count=forbidden_count + host_count,
        details=details,
        test_results=list(payload.get("tests") or []),
        duration_ms=duration_ms,
        raw_stdout=result.stdout,
        raw_stderr=result.stderr,
        returncode=result.returncode,
    )
