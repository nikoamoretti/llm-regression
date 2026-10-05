"""Configurable regression alert policy.

This is a policy, not a scientific law. Do not treat a 5 pp drop as proof that
OpenAI changed model weights.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class AlertDecision:
    severity: str
    status: str
    reason: str
    metric: str
    label: str = "none"


def load_alert_policy(path: Path | None = None) -> dict[str, Any]:
    default = {
        "warning": {"point_estimate_drop_pp": 5},
        "confirmed_regression": {
            "effect_drop_pp": 5,
            "upper_95_ci_below_zero": True,
            "persist_windows": 2,
        },
    }
    if path is None:
        candidate = Path("configs/alert_policy.yaml")
        path = candidate if candidate.exists() else None
    if path is None or not path.exists():
        return default
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    default.update(raw)
    return default


def evaluate_alert_policy(
    policy: dict[str, Any],
    *,
    absolute_delta: float,
    ci_high: float | None,
    consecutive_windows: int = 1,
    infrastructure_incident: bool = False,
) -> AlertDecision:
    if infrastructure_incident:
        return AlertDecision("info", "suppressed", "Known infrastructure incident", "strict_pass", "suppressed")
    drop_pp = -absolute_delta * 100.0
    warning = policy.get("warning") or {}
    confirmed = policy.get("confirmed_regression") or {}
    if (
        drop_pp >= float(confirmed.get("effect_drop_pp", 5))
        and (not confirmed.get("upper_95_ci_below_zero") or (ci_high is not None and ci_high < 0))
        and consecutive_windows >= int(confirmed.get("persist_windows", 2))
    ):
        return AlertDecision(
            "confirmed_regression",
            "open",
            "Configured confirmed-regression rule matched on this frozen benchmark",
            "strict_pass",
            "confirmed_regression",
        )
    if drop_pp >= float(warning.get("point_estimate_drop_pp", 5)):
        return AlertDecision(
            "warning",
            "open",
            "Point-estimate drop met the configured warning threshold",
            "strict_pass",
            "warning",
        )
    return AlertDecision("none", "ok", "No configured quality alert", "strict_pass", "none")


def quality_alert(
    *,
    absolute_delta: float,
    ci_high: float | None,
    n_tasks: int,
    consecutive_below: int = 0,
    infrastructure_incident: bool = False,
) -> AlertDecision:
    """Legacy helper kept for existing unit tests."""
    if infrastructure_incident:
        return AlertDecision("info", "suppressed", "Known infrastructure incident", "strict_pass")

    if absolute_delta <= -0.10 and ci_high is not None and ci_high < 0 and n_tasks >= 30:
        return AlertDecision(
            "critical",
            "open",
            "Paired bootstrap CI excludes zero and drop is at least 10 pp on >=30 tasks",
            "strict_pass",
        )

    if consecutive_below >= 3:
        return AlertDecision(
            "critical",
            "open",
            "Three consecutive full evaluations below control threshold",
            "strict_pass",
        )

    if absolute_delta <= -0.05:
        return AlertDecision(
            "warning",
            "open",
            "Pass rate is at least 5 pp below baseline",
            "strict_pass",
        )

    return AlertDecision("none", "ok", "No quality alert", "strict_pass")


def efficiency_alert(
    *,
    cost_ratio: float | None = None,
    wall_ratio: float | None = None,
    reasoning_ratio: float | None = None,
    quality_improved: bool = False,
) -> list[AlertDecision]:
    alerts: list[AlertDecision] = []
    if cost_ratio is not None and cost_ratio >= 1.25:
        alerts.append(
            AlertDecision("warning", "open", "Passing-run median cost +25% or more", "cost_per_pass")
        )
    if wall_ratio is not None and wall_ratio >= 1.30:
        alerts.append(
            AlertDecision(
                "warning",
                "open",
                "Passing-run median wall time +30% or more",
                "wall_time_per_pass",
            )
        )
    if reasoning_ratio is not None and reasoning_ratio >= 1.30 and not quality_improved:
        alerts.append(
            AlertDecision(
                "warning",
                "open",
                "Passing-run median reasoning tokens +30% without quality gain",
                "reasoning_tokens_per_pass",
            )
        )
    return alerts


def render_status_line(decision: AlertDecision) -> str:
    if decision.severity in {"critical", "confirmed_regression"}:
        return "🔴 CONFIRMED"
    if decision.severity == "warning":
        return "🟡 WARNING"
    return "🟢 STABLE"
