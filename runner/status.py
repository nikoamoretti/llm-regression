"""Explicit attempt statuses and the three reliability metrics.

quality_pass / quality_fail measure model behavior after a valid execution.
infra_fail measures provider/service availability, never model quality.
harness_fail is a runner/container bug and is excluded from model-quality statistics.
invalid_configuration means the requested model/effort/auth was not the one that ran.
"""

from __future__ import annotations

from typing import Any, Iterable

QUALITY_PASS = "quality_pass"
QUALITY_FAIL = "quality_fail"
INFRA_FAIL = "infra_fail"
HARNESS_FAIL = "harness_fail"
INVALID_CONFIGURATION = "invalid_configuration"

QUALITY_STATUSES = (QUALITY_PASS, QUALITY_FAIL)
VALID_EXECUTION_STATUSES = QUALITY_STATUSES
ALL_STATUSES = (
    QUALITY_PASS,
    QUALITY_FAIL,
    INFRA_FAIL,
    HARNESS_FAIL,
    INVALID_CONFIGURATION,
)

STATUS_DEFINITIONS = {
    QUALITY_PASS: (
        "Valid model execution finished inside budget; all required hidden acceptance "
        "and regression tests passed; no prohibited files were modified."
    ),
    QUALITY_FAIL: (
        "Valid model execution finished (or exhausted the task wall budget) but the "
        "final workspace did not meet the strict grader. Includes timeouts, incomplete "
        "stops, and broken patches. Never auto-retried."
    ),
    INFRA_FAIL: (
        "Provider/service failure before or during execution that is not a model-quality "
        "signal: HTTP 429, provider 5xx, or network connection failure."
    ),
    HARNESS_FAIL: (
        "Runner, container, or grader-host failure independent of the model. Excluded "
        "from model-quality statistics."
    ),
    INVALID_CONFIGURATION: (
        "Requested model/effort/auth could not be verified, or verified values differ "
        "from the request (for example Max was requested and High was applied)."
    ),
}


def classify_attempt(
    *,
    invalid_configuration: bool = False,
    infrastructure: bool = False,
    harness_error: bool = False,
    error_code: str | None = None,
    strict_pass: bool | None = None,
    timed_out: bool = False,
) -> str:
    if invalid_configuration or error_code in {"model_mismatch", "effort_mismatch", "auth_mismatch"}:
        return INVALID_CONFIGURATION
    if harness_error or error_code in {"harness", "sandbox_crash"}:
        return HARNESS_FAIL
    if infrastructure or error_code in {"http_429", "http_5xx", "network", "empty_crash", "process_crash"}:
        return INFRA_FAIL
    if timed_out or error_code == "timeout":
        return QUALITY_FAIL
    if strict_pass is True:
        return QUALITY_PASS
    return QUALITY_FAIL


def reliability_metrics(statuses: Iterable[str]) -> dict[str, Any]:
    """Report the three distinct reliability metrics.

    1. quality given valid execution = quality_pass / (quality_pass + quality_fail)
    2. operational availability     = valid model executions / scheduled attempts
    3. end-to-end success           = quality_pass / all scheduled attempts
    """
    items = list(statuses)
    scheduled = len(items)
    n_pass = sum(1 for item in items if item == QUALITY_PASS)
    n_fail = sum(1 for item in items if item == QUALITY_FAIL)
    n_valid = n_pass + n_fail
    n_infra = sum(1 for item in items if item == INFRA_FAIL)
    n_harness = sum(1 for item in items if item == HARNESS_FAIL)
    n_invalid = sum(1 for item in items if item == INVALID_CONFIGURATION)
    quality = (n_pass / n_valid) if n_valid else None
    availability = (n_valid / scheduled) if scheduled else None
    end_to_end = (n_pass / scheduled) if scheduled else None
    return {
        "scheduled_attempts": scheduled,
        "quality_pass": n_pass,
        "quality_fail": n_fail,
        "infra_fail": n_infra,
        "harness_fail": n_harness,
        "invalid_configuration": n_invalid,
        "valid_executions": n_valid,
        "quality_given_valid": quality,
        "operational_availability": availability,
        "end_to_end_success": end_to_end,
    }


def task_balanced_scores(task_to_statuses: dict[str, Iterable[str]]) -> dict[str, Any]:
    """Repeated attempts are not extra tasks. Each task gets equal weight."""
    task_scores = {}
    for task_id, statuses in task_to_statuses.items():
        valid = [1.0 if item == QUALITY_PASS else 0.0 for item in statuses if item in QUALITY_STATUSES]
        task_scores[task_id] = (sum(valid) / len(valid)) if valid else None
    present = [value for value in task_scores.values() if value is not None]
    suite = (sum(present) / len(present)) if present else None
    return {"task_scores": task_scores, "suite_score": suite, "n_tasks": len(present)}
