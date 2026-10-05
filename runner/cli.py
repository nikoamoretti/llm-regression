"""Unified CLI for the Grok / Astra longitudinal harness."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# Subcommands that own their argument parsing (argparse.REMAINDER drops a
# leading --option, so these are dispatched before the top-level parser runs).
PASSTHROUGH = {
    "behavior": "runner.behavior",
    "calibrate": "runner.calibrate",
    "mine": "runner.mine",
    "monitor": "runner.monitor",
    "plan": "runner.plan",
    "judge": "runner.judge",
    "results": "runner.results",
    "rotation": "runner.rotation",
    "site": "runner.report_site",
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in PASSTHROUGH:
        import importlib

        return importlib.import_module(PASSTHROUGH[argv[0]]).main(argv[1:])
    parser = argparse.ArgumentParser(prog="llm-regression")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="Prove a benchmark configuration is valid")
    doctor.add_argument("--track", default="")
    doctor.add_argument("--model", default="")
    doctor.add_argument("--models", default="")
    doctor.add_argument("--tracks", default="")
    doctor.add_argument("--effort", default="xhigh")
    doctor.add_argument("--allow-live-probe", action="store_true")
    doctor.add_argument("--offline", action="store_true")
    doctor.add_argument("--live", action="store_true")
    doctor.add_argument("--json", action="store_true")

    sub.add_parser("validate-tasks", help="Validate task manifests")
    vh = sub.add_parser("verify-hashes", help="Verify or rewrite frozen hashes")
    vh.add_argument("--write", action="store_true")

    tg = sub.add_parser("test-graders", help="Gold/broken/negative grader self-test")
    tg.add_argument("--task")
    tg.add_argument("--suite")
    tg.add_argument("--fast", action="store_true")

    ev = sub.add_parser("evaluate", help="Run an evaluation suite")
    ev.add_argument("--suite", default="canary")
    ev.add_argument("--model", default="")
    ev.add_argument("--models", default="")
    ev.add_argument("--efforts", default="low,medium,high,xhigh")
    ev.add_argument("--track", default="")
    ev.add_argument("--tracks", default="")
    ev.add_argument("--client-mode", default="latest", choices=["pinned", "latest"])
    ev.add_argument("--repeats", type=int)
    ev.add_argument("--source", choices=["model", "gold", "none", "fake"], default="model")
    ev.add_argument("--fresh", action="store_true")
    ev.add_argument("--workers", type=int, default=1)
    ev.add_argument("--artifact-dir", default="artifacts/runs")
    ev.add_argument("--seed", type=int, default=20260911)
    ev.add_argument("--schedule-seed", type=int)
    ev.add_argument("--bootstrap-seed", type=int, default=20260911)
    ev.add_argument("--fixture-seed", type=int, default=20260911)
    ev.add_argument("--grader-seed", type=int, default=20260911)
    ev.add_argument("--blocked-randomization", action="store_true")
    ev.add_argument("--fake-scenario", default="ok")
    ev.add_argument("--trigger", default="cli", help="recorded on the run, e.g. schedule")
    ev.add_argument("--tasks", default="", help="comma-separated subset of the suite's tasks")

    baseline = sub.add_parser("baseline", help="Create, show, list, lock, or qualify baselines")
    bsub = baseline.add_subparsers(dest="baseline_command", required=True)
    bcreate = bsub.add_parser("create")
    bcreate.add_argument("--runs", required=True, help="Comma-separated run IDs")
    bcreate.add_argument("--name", required=True)
    bcreate.add_argument("--track", required=True)
    bcreate.add_argument("--model", required=True)
    bcreate.add_argument("--effort", required=True)
    bcreate.add_argument("--suite", default="canary")
    bcreate.add_argument("--notes", default="")
    bshow = bsub.add_parser("show")
    bshow.add_argument("name")
    bsub.add_parser("list")
    block = bsub.add_parser("lock")
    block.add_argument("name")
    bqual = bsub.add_parser("qualify")
    bqual.add_argument("--runs", required=True)

    cmp_ = sub.add_parser("compare", help="Compare a run to a locked baseline")
    cmp_.add_argument("--baseline", required=True)
    cmp_.add_argument("--current", required=True)
    cmp_.add_argument("--force", action="store_true")

    power = sub.add_parser("power", help="Power analysis from a baseline")
    power.add_argument("--baseline", required=True)
    power.add_argument("--effort", default="xhigh")
    power.add_argument("--minimum-drop-pp", type=float, default=8.0)
    power.add_argument("--target-power", type=float, default=0.80)
    power.add_argument("--alpha", type=float, default=0.05)
    power.add_argument("--candidate-task-counts", default="10,20,30,50")
    power.add_argument("--candidate-repeats", default="1,3,5")
    power.add_argument("--json", action="store_true")

    images = sub.add_parser("images", help="Build or show the pinned agent/grader images")
    images.add_argument("images_args", nargs=argparse.REMAINDER)
    egress = sub.add_parser("egress", help="Agent egress proxy: up, down, status, logs")
    egress.add_argument("egress_action", choices=["up", "down", "status", "logs"])
    sub.add_parser("calibrate", help="Per-task pass rates: which tasks carry signal", add_help=False)
    sub.add_parser("plan", help="Size a series: what drop can a usage budget detect", add_help=False)
    sub.add_parser("mine", help="Turn a real bug-fix commit into a frozen task", add_help=False)
    sub.add_parser("monitor", help="Drift monitor: rolling pass rate, paired change, CUSUM alarms", add_help=False)

    dash = sub.add_parser("dashboard", help="Dashboard commands")
    dsub = dash.add_subparsers(dest="dashboard_command", required=True)
    dsub.add_parser("build")
    sub.add_parser("report", help="Alias for dashboard build")

    an = sub.add_parser("analyze", help="Legacy analyze alias")
    an.add_argument("--current", required=True)
    an.add_argument("--baseline")
    an.add_argument("--fail-on-confirmed-regression", action="store_true")

    args = parser.parse_args(argv)
    root = Path.cwd()

    if args.command == "doctor":
        from runner.doctor import main as impl

        extra = ["--effort", args.effort]
        if args.track:
            extra.extend(["--track", args.track])
        if args.model:
            extra.extend(["--model", args.model])
        if args.models:
            extra.extend(["--models", args.models])
        if args.tracks:
            extra.extend(["--tracks", args.tracks])
        if args.allow_live_probe:
            extra.append("--allow-live-probe")
        if args.offline:
            extra.append("--offline")
        if args.live:
            extra.append("--live")
        if args.json:
            extra.append("--json")
        return impl(extra)
    if args.command == "images":
        from runner.images import main as impl

        return impl(args.images_args)
    if args.command == "egress":
        from runner import egress as egress_mod

        if args.egress_action == "up":
            from runner.images import recorded_image

            toolchain = recorded_image("toolchain")
            if not toolchain:
                print("toolchain image not built: run `python -m runner images build`", file=sys.stderr)
                return 1
            print(json.dumps(egress_mod.ensure_egress(toolchain, egress_mod.allowlist("claude_code_cli")).__dict__))
        elif args.egress_action == "down":
            egress_mod.egress_down()
        elif args.egress_action == "status":
            print(json.dumps(egress_mod.status()))
        else:
            for record in egress_mod.decisions():
                print(json.dumps(record, sort_keys=True))
        return 0
    if args.command == "validate-tasks":
        from runner.validate_tasks import main as impl

        return impl([])
    if args.command == "verify-hashes":
        from runner.verify_hashes import main as impl

        return impl(["--write"] if args.write else [])
    if args.command == "test-graders":
        from runner.test_graders import main as impl

        extra = []
        if args.task:
            extra.extend(["--task", args.task])
        if args.suite:
            extra.extend(["--suite", args.suite])
        if args.fast:
            extra.append("--fast")
        return impl(extra)
    if args.command == "evaluate":
        from runner.evaluate import main as impl

        extra = [
            "--suite",
            args.suite,
            "--efforts",
            args.efforts,
            "--client-mode",
            args.client_mode,
            "--source",
            args.source,
            "--artifact-dir",
            args.artifact_dir,
            "--workers",
            str(args.workers),
            "--seed",
            str(args.seed),
            "--bootstrap-seed",
            str(args.bootstrap_seed),
            "--fixture-seed",
            str(args.fixture_seed),
            "--grader-seed",
            str(args.grader_seed),
            "--fake-scenario",
            args.fake_scenario,
            "--trigger",
            args.trigger,
            "--tasks",
            args.tasks,
        ]
        if args.model:
            extra.extend(["--model", args.model])
        if args.models:
            extra.extend(["--models", args.models])
        if args.track:
            extra.extend(["--track", args.track])
        if args.tracks:
            extra.extend(["--tracks", args.tracks])
        if args.schedule_seed is not None:
            extra.extend(["--schedule-seed", str(args.schedule_seed)])
        if args.repeats is not None:
            extra.extend(["--repeats", str(args.repeats)])
        if args.fresh:
            extra.append("--fresh")
        if args.blocked_randomization:
            extra.append("--blocked-randomization")
        return impl(extra)
    if args.command == "baseline":
        return _baseline(args, root)
    if args.command == "compare":
        from runner.compare import main as impl

        extra = ["--baseline", args.baseline, "--current", args.current]
        if args.force:
            extra.append("--force")
        return impl(extra)
    if args.command == "power":
        from runner.power import main as impl

        extra = [
            "--baseline",
            args.baseline,
            "--effort",
            args.effort,
            "--minimum-drop-pp",
            str(args.minimum_drop_pp),
            "--target-power",
            str(args.target_power),
            "--alpha",
            str(args.alpha),
            "--candidate-task-counts",
            args.candidate_task_counts,
            "--candidate-repeats",
            args.candidate_repeats,
        ]
        if args.json:
            extra.append("--json")
        return impl(extra)
    if args.command in {"dashboard", "report"}:
        from runner.dashboard import write_dashboard
        from runner.storage import Store

        path = write_dashboard(Store(), root / "artifacts" / "dashboard")
        print(f"Wrote {path}")
        return 0
    if args.command == "analyze":
        if args.baseline:
            from runner.compare import main as impl

            return impl(["--baseline", args.baseline, "--current", args.current])
        from runner.analyze import main as impl

        extra = ["--current", args.current]
        if args.fail_on_confirmed_regression:
            extra.append("--fail-on-confirmed-regression")
        return impl(extra)
    return 2


def _baseline(args: argparse.Namespace, root: Path) -> int:
    from runner.baselines import (
        create_baseline,
        list_baselines,
        lock_baseline,
        qualify_runs,
        show_baseline,
    )
    from runner.config import load_suites
    from runner.coordinator import runner_git_sha
    from runner.storage import Store
    from runner.tasks import select_tasks, suite_hash

    store = Store()
    if args.baseline_command == "list":
        print(json.dumps(list_baselines(store), indent=2, default=str))
        return 0
    if args.baseline_command == "show":
        print(json.dumps(show_baseline(store, args.name), indent=2, default=str))
        return 0
    if args.baseline_command == "lock":
        print(json.dumps(lock_baseline(store, args.name), indent=2, default=str))
        return 0
    if args.baseline_command == "qualify":
        report = qualify_runs(store, [item.strip() for item in args.runs.split(",") if item.strip()])
        print(json.dumps(report, indent=2, default=str))
        return 0 if report["ok"] else 1
    if args.baseline_command == "create":
        suites = load_suites(root / "configs" / "suites.yaml")
        suite = suites[args.suite]
        tasks = select_tasks(root / "tasks", suite.tasks)
        hashed = suite_hash(tasks, suite.name, suite.version)
        baseline_id = create_baseline(
            store,
            name=args.name,
            run_ids=[item.strip() for item in args.runs.split(",") if item.strip()],
            track=args.track,
            model=args.model,
            effort=args.effort,
            suite_id=suite.name,
            suite_version=suite.version,
            suite_hash=hashed,
            harness_commit=runner_git_sha(root),
            notes=args.notes,
        )
        print(json.dumps({"baseline_id": baseline_id, "name": args.name}, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())


def run() -> None:
    raise SystemExit(main())
