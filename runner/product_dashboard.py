"""Collect the product series (Claude Code, Cursor) for the results site.

Reads the results database (and the drift monitor's JSON, when present) into
one JSON-ready dict: runs, attempts with their grades, quality scores and
behaviour metrics, and the monitor's verdict per series. ``runner.report_site``
renders it.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

from runner import CLAUDE_CODE_MODEL, CLAUDE_CODE_TRACK, CURSOR_MODEL, CURSOR_TRACK, PRODUCT_TRACKS
from runner.behavior import BEHAVIOR_VERSION, FLAGS
from runner.judge import DEFAULT_JUDGE_EFFORT, DEFAULT_JUDGE_MODEL, DIMENSIONS, RUBRIC_VERSION

# The products on the page, in order. A product with no runs yet is shown with its setup step.
PRODUCTS = [
    {"track": CLAUDE_CODE_TRACK, "model": CLAUDE_CODE_MODEL, "name": "Opus 5.5", "via": "Claude Code",
     "setup": "Runs daily on the Claude subscription."},
    {"track": CURSOR_TRACK, "model": CURSOR_MODEL, "name": "Grok 4.7", "via": "Cursor",
     "setup": "Starts with the first daily run after a Cursor API key (CURSOR_API_KEY) is added."},
]


TASKS_DIR = Path(__file__).resolve().parents[1] / "tasks"


def task_title(task_key: str, tasks_dir: Path = TASKS_DIR) -> str | None:
    """A plain one-line title for a task: the first line of its prompt that is not just the task id."""
    prompts = sorted(tasks_dir.glob(f"{task_key}/v*/prompt.md"))
    if not prompts:
        return None
    for line in prompts[-1].read_text(encoding="utf-8").splitlines():
        text = line.strip().lstrip("#").strip().replace("`", "")
        if text and text != task_key:
            return text if len(text) <= 110 else text[:107].rsplit(" ", 1)[0] + "…"
    return None


def collect(db_path: Path, monitor_json: Path | None = None) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    marks = ",".join("?" * len(PRODUCT_TRACKS))
    runs = [dict(row) for row in conn.execute(
        f"""SELECT run_id, started_at, requested_model AS model, requested_effort AS effort, status, track
           FROM eval_runs WHERE track IN ({marks}) AND scientific_data = 1 ORDER BY started_at""",
        PRODUCT_TRACKS,
    )]
    judgments: dict[str, list[dict[str, Any]]] = defaultdict(list)
    judge_model = None
    if "judgments" in tables:
        for row in conn.execute(
            "SELECT attempt_id, overall, scores, summary, unsupported_claims, judge_model FROM judgments "
            # One judge per series: scores from different judges are not comparable.
            "WHERE status = 'ok' AND subject = 'attempt' AND rubric_version = ? AND judge_model = ? "
            "AND judge_effort = ?", (RUBRIC_VERSION, DEFAULT_JUDGE_MODEL, DEFAULT_JUDGE_EFFORT)
        ):
            judgments[row["attempt_id"]].append(dict(row))
            judge_model = row["judge_model"]
    behaviors: dict[str, dict[str, Any]] = {}
    raw_usage: dict[str, dict[str, float]] = {}
    if "behaviors" in tables:
        for row in conn.execute("SELECT attempt_id, metrics, flags FROM behaviors WHERE behavior_version = ?",
                                (BEHAVIOR_VERSION,)):
            metrics = json.loads(row["metrics"])
            raw_usage[row["attempt_id"]] = metrics.get("plan_usage_peak") or {}
            behaviors[row["attempt_id"]] = {
                "flags": sorted(name for name, on in json.loads(row["flags"]).items() if on),
                **{key: metrics.get(key) for key in ("tool_calls", "num_turns", "test_runs", "other_checks",
                                                     "edits", "tool_errors", "output_tokens", "rate_limited_events",
                                                     "files_changed", "lines_added", "lines_removed")},
            }
    attempts = []
    for row in conn.execute(
        f"""SELECT a.attempt_id, a.run_id, a.quality_status, a.error_code, a.wall_time_ms, a.cost_usd_ticks,
                  a.requested_effort AS effort, r.track, r.started_at, t.task_key, t.difficulty, t.category
           FROM attempts a JOIN tasks t ON t.task_version_id = a.task_version_id
           JOIN eval_runs r ON r.run_id = a.run_id
           WHERE r.track IN ({marks}) AND r.scientific_data = 1""",
        PRODUCT_TRACKS,
    ):
        item = dict(row)
        found = judgments.get(item["attempt_id"], [])
        if found:
            per_dim = {name: mean(json.loads(j["scores"])[name] for j in found) for name in DIMENSIONS}
            item["quality"] = {
                "overall": round(mean(j["overall"] for j in found), 2),
                "dims": per_dim,
                "summary": found[0]["summary"],
                "claims": json.loads(found[0]["unsupported_claims"] or "[]"),
                "repeats": len(found),
            }
        if item["attempt_id"] in behaviors:
            item["behavior"] = behaviors[item["attempt_id"]]
        if item["attempt_id"] in behaviors and item["attempt_id"] in raw_usage:
            item["behavior"]["plan_usage_peak"] = raw_usage[item["attempt_id"]]
        item["minutes"] = round(item.pop("wall_time_ms") / 60000, 2) if item.get("wall_time_ms") else None
        ticks = item.pop("cost_usd_ticks")
        item["cost_usd"] = round(ticks / 1e10, 3) if ticks else None
        item["source"] = "mined" if item["task_key"].startswith("MINE-") else "original"
        item["date"] = item.pop("started_at")[:10]
        attempts.append(item)
    by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in attempts:
        by_run[item["run_id"]].append(item)
    for run in runs:
        items = by_run.get(run["run_id"], [])
        peaks = [a["behavior"].get("plan_usage_peak") or {} for a in items if a.get("behavior")]
        run["plan_usage_peak"] = {
            name: round(max(p.get(name, 0) for p in peaks), 3) for name in ("five_hour", "seven_day")
            if any(name in p for p in peaks)
        }
        graded = [a for a in items if a["quality_status"] in ("quality_pass", "quality_fail")]
        passed = [a for a in graded if a["quality_status"] == "quality_pass"]
        scored = [a["quality"]["overall"] for a in items if a.get("quality")]
        minutes = [a["minutes"] for a in graded if a["minutes"]]
        run.update({
            "date": run["started_at"][:10],
            "attempts": len(items), "graded": len(graded), "passed": len(passed),
            "not_graded": len(items) - len(graded),
            "pass_rate": round(len(passed) / len(graded), 4) if graded else None,
            "quality": round(mean(scored), 2) if scored else None,
            "median_minutes": round(median(minutes), 1) if minutes else None,
            "cost_usd": round(sum(a["cost_usd"] or 0 for a in items), 2),
        })
    monitor = []
    if monitor_json and monitor_json.exists():
        monitor = [{"series": m.get("series"), "status": m.get("status"), "message": m.get("message"),
                    "reference": m.get("reference"), "current": m.get("current"), "warning": m.get("warning")}
                   for m in json.loads(monitor_json.read_text(encoding="utf-8"))]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "products": PRODUCTS,
        "runs": runs,
        "attempts": attempts,
        "dimensions": {name: text.split("?")[0] + "?" for name, text in DIMENSIONS.items()},
        "judge_model": judge_model,
        "rubric": RUBRIC_VERSION,
        "flags": FLAGS,
        "behavior_version": BEHAVIOR_VERSION,
        "monitor": monitor,
        "task_titles": {key: title for key in sorted({a["task_key"] for a in attempts})
                        if (title := task_title(key))},
    }
