"""Prove every official task: broken fixture fails, gold passes, negatives fail."""

from __future__ import annotations

import argparse
from pathlib import Path

from runner.artifacts import ArtifactStore
from runner.config import load_suites, model_config_from_json
from runner.coordinator import Coordinator
from runner.isolation import probe_from_workspace, workspace_leak_report
from runner.storage import Store
from runner.tasks import GRADERS_HINT, select_tasks


def gold_config() -> object:
    return model_config_from_json(
        {
            "provider": "offline",
            "model": "gpt-5.6-sol",
            "reasoning": {"effort": "max"},
            "temperature": 0,
            "top_p": 1,
            "max_output_tokens": 16384,
            "parallel_tool_calls": False,
            "store": False,
            "system_prompt_version": "codex-frozen-v1",
            "toolset_version": "codex-cli-v1",
            "track": "codex_product",
        }
    )


def run_task_selftest(repo_root: Path, task_id: str, repeats: int = 2) -> list[str]:
    errors: list[str] = []
    task = select_tasks(repo_root / "tasks", [task_id])[0]
    if not task.has_graders:
        return [f"{task_id}: {GRADERS_HINT}"]
    store = Store("sqlite://")
    artifacts = ArtifactStore(repo_root / "artifacts" / "private" / "grader-tests")
    coordinator = Coordinator(store, artifacts, provider=None, repo_root=repo_root)
    suite_id, task_version_id = coordinator.ensure_suite_and_task("grader-selftest", task.version, task)
    config = gold_config()
    coordinator.ensure_config(config, "tools", "system")
    run_broken = store.create_run(
        {
            "suite_id": suite_id,
            "config_id": coordinator.ensure_config(config, "tools", "system"),
            "trigger": "grader-selftest",
            "status": "running",
            "runner_git_sha": "selftest",
        }
    )
    run_gold = store.create_run(
        {
            "suite_id": suite_id,
            "config_id": coordinator.ensure_config(config, "tools", "system"),
            "trigger": "grader-selftest",
            "status": "running",
            "runner_git_sha": "selftest",
        }
    )
    broken_flags = []
    gold_flags = []
    for trial in range(repeats):
        broken = coordinator.run_attempt(
            run_id=run_broken,
            suite_version="selftest",
            task=task,
            task_version_id=task_version_id,
            config=config,
            trial_index=trial,
            source="none",
        )
        gold = coordinator.run_attempt(
            run_id=run_gold,
            suite_version="selftest",
            task=task,
            task_version_id=task_version_id,
            config=config,
            trial_index=trial,
            source="gold",
        )
        if broken.grade is None:
            errors.append(f"{task_id} broken trial {trial} produced no grade: {broken.error}")
        else:
            broken_flags.append(broken.grade.strict_pass)
            if broken.grade.strict_pass:
                errors.append(f"{task_id} broken fixture unexpectedly passed on trial {trial}")
        if gold.grade is None:
            errors.append(f"{task_id} gold trial {trial} produced no grade: {gold.error}")
        else:
            gold_flags.append(gold.grade.strict_pass)
            if not gold.grade.strict_pass:
                errors.append(
                    f"{task_id} gold patch failed on trial {trial}: "
                    f"functional={gold.grade.functional_score} "
                    f"regression={gold.grade.regression_score} "
                    f"constraints={gold.grade.constraints_score} "
                    f"details={gold.grade.details}"
                )
    if broken_flags and len(set(broken_flags)) != 1:
        errors.append(f"{task_id} broken grading is not deterministic: {broken_flags}")
    if gold_flags and len(set(gold_flags)) != 1:
        errors.append(f"{task_id} gold grading is not deterministic: {gold_flags}")

    if task.negatives_dir.exists():
        run_neg = store.create_run(
            {
                "suite_id": suite_id,
                "config_id": coordinator.ensure_config(config, "tools", "system"),
                "trigger": "grader-selftest-negative",
                "status": "running",
                "runner_git_sha": "selftest",
            }
        )
        for index, patch in enumerate(sorted(task.negatives_dir.glob("*.patch"))):
            negative = coordinator.run_attempt(
                run_id=run_neg,
                suite_version="selftest",
                task=task,
                task_version_id=task_version_id,
                config=config,
                trial_index=index,
                source="negative",
                negative_name=patch.name,
            )
            if negative.grade is None:
                errors.append(f"{task_id} negative {patch.name} produced no grade: {negative.error}")
            elif negative.grade.strict_pass:
                errors.append(f"{task_id} negative {patch.name} unexpectedly passed")

    leaks = workspace_leak_report(task.fixture_path, task.grader_path)
    if leaks:
        errors.append(f"{task_id} fixture leaked grader material: {leaks}")
    visible = probe_from_workspace(
        task.fixture_path,
        [Path("gold.patch"), Path("hidden_tests"), Path("grader"), Path("private_graders")],
    )["visible"]
    if visible:
        errors.append(f"{task_id} fixture can see private grader paths: {visible}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task")
    parser.add_argument("--suite")
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args(argv)
    repo_root = Path.cwd()
    if args.task:
        keys = [args.task]
    elif args.suite:
        suites = load_suites(repo_root / "configs" / "suites.yaml")
        keys = suites[args.suite].tasks
    else:
        keys = []
        seen = set()
        for task in select_tasks(repo_root / "tasks"):
            if task.id not in seen:
                seen.add(task.id)
                keys.append(task.id)
    repeats = 1 if args.fast else args.repeats
    all_errors = []
    report_rows = []
    for key in keys:
        print(f"[test-graders] {key}", flush=True)
        errors = run_task_selftest(repo_root, key, repeats=repeats)
        if errors:
            all_errors.extend(errors)
            report_rows.append({"task": key, "ok": False, "errors": errors})
            for error in errors:
                print(f"  FAIL {error}")
        else:
            report_rows.append({"task": key, "ok": True, "errors": []})
            print("  ok")
    out = repo_root / "artifacts" / "task-validation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    import json

    out.write_text(json.dumps(report_rows, indent=2), encoding="utf-8")
    if all_errors:
        return 1
    print("All graders accepted gold, rejected the broken fixture, and rejected negatives.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
