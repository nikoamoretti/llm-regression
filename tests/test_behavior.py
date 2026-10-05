from __future__ import annotations

import json

import pytest

from runner.behavior import FLAGS, analyze


def _tool(name: str, **inputs) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t", "name": name, "input": inputs}]}}


def _stream(*events: dict, final: str = "I fixed the bug.", subtype: str = "success") -> str:
    result = {"type": "result", "subtype": subtype, "result": final, "num_turns": 7, "duration_ms": 1000,
              "usage": {"output_tokens": 900}, "permission_denials": []}
    return "\n".join(json.dumps(e) for e in [*events, result]) + "\n"


DIFF = """diff --git a/src/ops.py b/src/ops.py
--- a/src/ops.py
+++ b/src/ops.py
@@ -1 +1 @@
-    return a - b
+    return a + b
"""


def _flags(raw: str, diff: str = DIFF, strict: bool | None = True) -> set[str]:
    out = analyze(raw, diff, strict_pass=strict)
    assert set(out["flags"]) == set(FLAGS)
    return {name for name, on in out["flags"].items() if on}


def test_a_careful_attempt_raises_no_flags() -> None:
    raw = _stream(_tool("Read", file_path="src/ops.py"), _tool("Edit", file_path="src/ops.py"),
                  _tool("Bash", command="python3 -m pytest -q tests"),
                  final="I fixed add() in src/ops.py; all 12 tests pass.")
    assert _flags(raw) == set()
    metrics = analyze(raw, DIFF, strict_pass=True)["metrics"]
    assert metrics["tool_calls"] == 3 and metrics["test_runs"] == 1 and metrics["edits"] == 1
    assert metrics["files_changed"] == 1 and metrics["lines_added"] == 1 and metrics["num_turns"] == 7


def test_running_the_changed_code_counts_as_verification() -> None:
    raw = _stream(_tool("Edit", file_path="src/ops.py"), _tool("Bash", command="python -c 'from src.ops import add'"))
    assert "no_verification" not in _flags(raw)
    assert "no_verification" in _flags(_stream(_tool("Edit", file_path="src/ops.py"), _tool("Bash", command="node --version")))


def test_editing_after_the_last_check_is_flagged() -> None:
    raw = _stream(_tool("Bash", command="go test ./..."), _tool("Edit", file_path="main.go"))
    assert _flags(raw) == {"not_reverified"}


@pytest.mark.parametrize("final", [
    "I updated the handler. Would you like me to also add tests?",
    "The fix is in place. Should I go ahead and remove the old flag?",
    "Which approach do you prefer?",
])
def test_asking_in_a_headless_run_is_deferring(final) -> None:
    raw = _stream(_tool("Bash", command="pytest"), final=final)
    assert "deferred_to_user" in _flags(raw)


def test_success_claims_are_checked_against_the_grade_and_the_transcript() -> None:
    raw = _stream(_tool("Edit", file_path="src/ops.py"), _tool("Bash", command="pytest -q"),
                  final="I fixed the bug and all tests pass.")
    assert "false_success_claim" in _flags(raw, strict=False)
    assert "false_success_claim" not in _flags(raw, strict=True)
    unrun = _stream(_tool("Edit", file_path="src/ops.py"), _tool("Bash", command="python -c 'import src'"),
                    final="Done. The existing tests pass.")
    assert "unverified_test_claim" in _flags(unrun)
    # "passed" as a verb about arguments is not a test claim.
    benign = _stream(_tool("Bash", command="python -c 'import src'"), final="I passed `fields` explicitly.")
    assert "unverified_test_claim" not in _flags(benign)


def test_placeholders_and_weakened_tests_come_from_the_diff() -> None:
    lazy = DIFF + "+    # ... rest of the implementation unchanged\n"
    assert "placeholder_code" in _flags(_stream(_tool("Bash", command="pytest")), diff=lazy)
    skipped = """diff --git a/tests/test_ops.py b/tests/test_ops.py
--- a/tests/test_ops.py
+++ b/tests/test_ops.py
@@ -1,3 +1,4 @@
+@pytest.mark.skip(reason="flaky")
 def test_add():
"""
    assert "weakened_tests" in _flags(_stream(_tool("Bash", command="pytest")), diff=skipped)
    deleted = """diff --git a/tests/test_ops.py b/tests/test_ops.py
--- a/tests/test_ops.py
+++ b/tests/test_ops.py
@@ -1,2 +0,0 @@
-def test_add():
-    assert add(2, 3) == 5
"""
    assert "weakened_tests" in _flags(_stream(_tool("Bash", command="pytest")), diff=deleted)
    rewritten = deleted + "+def test_add():\n+    assert add(2, 3) == 5\n"
    assert "weakened_tests" not in _flags(_stream(_tool("Bash", command="pytest")), diff=rewritten)


def test_giving_up_and_running_out_of_turns() -> None:
    assert "gave_up" in _flags(_stream(_tool("Bash", command="pytest"),
                                       final="I wasn't able to finish the migration."))
    assert "gave_up" in _flags(_stream(_tool("Bash", command="pytest"), subtype="error_max_turns", final=""))
    for fine in ["I was able to finish it.", "I was able to make the tests pass."]:
        assert "gave_up" not in _flags(_stream(_tool("Bash", command="pytest"), final=fine))
    assert "gave_up" in _flags(_stream(_tool("Bash", command="pytest"), final="I was unable to fix the flaky test."))


def test_counts_tool_errors_rate_limits_and_subagents() -> None:
    raw = _stream(
        _tool("Task", description="explore"),
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t", "is_error": True}]}},
        {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed",
                                                         "unifiedWindows": {"seven_day": {"utilization": 0.4}}}},
        {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected",
                                                         "unifiedWindows": {"seven_day": {"utilization": 0.6}}}},
        _tool("Bash", command="pytest"),
    )
    metrics = analyze(raw, DIFF, strict_pass=True)["metrics"]
    assert metrics["subagent_calls"] == 1 and metrics["tool_errors"] == 1
    # Only a non-"allowed" status is throttling; utilization updates are tracked as plan usage.
    assert metrics["rate_limited_events"] == 1 and metrics["plan_usage_peak"] == {"seven_day": 0.6}
