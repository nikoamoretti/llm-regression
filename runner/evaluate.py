"""Run a frozen evaluation suite against Grok, Astra, or optional Codex."""

from __future__ import annotations

import argparse
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from runner import (
    CANONICAL_EFFORTS,
    CANONICAL_MODELS,
    CANONICAL_TRACKS,
    CLAUDE_CODE_MODEL,
    CLAUDE_CODE_PROVIDER,
    CLAUDE_CODE_TRACK,
    CONTROL_TRACK,
    CURSOR_MODEL,
    CURSOR_PROVIDER,
    CURSOR_TRACK,
    PRIMARY_PROVIDER,
    PRIMARY_TRACK,
    REQUEST_MODEL,
    ULTRA_TRACK,
)
from runner.artifacts import ArtifactStore
from runner.config import ModelConfig, SuiteSpec, load_suites, model_config_from_json
from runner.coordinator import Coordinator, runner_git_sha
from runner.hash_tree import canonical_hash
from runner.identity import assert_single_agent_effort
from runner.models import validate_model_effort_track
from runner.pairing import blocked_model_order, pair_key
from runner.provenance import collect_environment_manifest
from runner.protocols import pairing_protocol_version, tool_protocol_version
from runner.providers.factory import make_provider
from runner.schedule import build_schedule
from runner.scientific import is_scientific_source
from runner.storage import Store
from runner.tasks import require_graders, select_tasks, suite_hash


def _config_for(
    *,
    track: str,
    model: str,
    effort: str,
    client_mode: str,
    extra: dict | None = None,
) -> ModelConfig:
    if effort == "ultra":
        track = ULTRA_TRACK
    elif track not in {*CANONICAL_TRACKS, CONTROL_TRACK, ULTRA_TRACK, CLAUDE_CODE_TRACK, CURSOR_TRACK} and (
        track != PRIMARY_TRACK
    ):
        raise ValueError(f"unknown track {track}")
    if track == PRIMARY_TRACK and effort != "ultra":
        assert_single_agent_effort(effort)
    if track in CANONICAL_TRACKS:
        spec = validate_model_effort_track(model, effort, track)
        provider = spec.provider
        auth = "api_key"
        send_sampling = spec.send_sampling_params
        system = "api-frozen-v1"
        tools = "local-tools-v2" if track == "agentic" else "none"
    elif track == CLAUDE_CODE_TRACK:
        validate_model_effort_track(model, effort, track)
        provider = CLAUDE_CODE_PROVIDER
        auth = "claude_subscription"
        # Claude Code exposes no sampling parameters; the product system prompt
        # is Claude Code's own, identified by CLI version in provenance.
        send_sampling = False
        system = "claude-code-default-v1"
        tools = "claude-code-restricted-v1"
    elif track == CURSOR_TRACK:
        validate_model_effort_track(model, effort, track)
        provider = CURSOR_PROVIDER
        auth = "cursor_subscription"
        # Cursor exposes no sampling parameters; its system prompt is identified
        # by CLI build in provenance, its config by toolset_version.
        send_sampling = False
        system = "cursor-default-v1"
        tools = "cursor-cli-v2"
    elif track == CONTROL_TRACK:
        provider = "openai_responses"
        auth = "api_key"
        send_sampling = True
        system = "codex-frozen-v1"
        tools = "none"
    else:
        provider = PRIMARY_PROVIDER
        auth = "chatgpt"
        send_sampling = True
        system = "codex-frozen-v1"
        tools = "codex-cli-v1"
    payload = {
        "provider": provider,
        "model": model,
        "reasoning": {"effort": effort},
        "max_output_tokens": 16384,
        "parallel_tool_calls": False,
        "store": False,
        "system_prompt_version": system,
        "toolset_version": tools,
        "track": track,
        "client_mode": client_mode,
        "auth_surface": auth,
        "send_sampling_params": send_sampling,
        "protocol_version": pairing_protocol_version(),
        "tool_protocol_version": tool_protocol_version(),
    }
    if send_sampling:
        payload["temperature"] = 0
        payload["top_p"] = 1
    if extra:
        payload.update(extra)
    return model_config_from_json(payload)


def _parse_csv(value: str | None, default: list[str]) -> list[str]:
    if not value:
        return list(default)
    return [item.strip() for item in value.split(",") if item.strip()]


def evaluate(
    repo_root: Path,
    suite_name: str,
    efforts: list[str] | None = None,
    source: str = "model",
    workers: int = 1,
    artifact_dir: Path | None = None,
    database_url: str | None = None,
    trigger: str = "manual",
    run_seed: int = 20260911,
    track: str | None = None,
    tracks: list[str] | None = None,
    model: str | None = None,
    models: list[str] | None = None,
    repeats: int | None = None,
    client_mode: str = "latest",
    fresh: bool = False,
    blocked_randomization: bool = False,
    bootstrap_seed: int = 20260911,
    fixture_seed: int = 20260911,
    grader_seed: int = 20260911,
    fake_scenario: str = "ok",
    only_tasks: list[str] | None = None,
) -> dict[str, str]:
    suites = load_suites(repo_root / "configs" / "suites.yaml")
    if suite_name not in suites:
        raise KeyError(f"Unknown suite: {suite_name}. Known: {sorted(suites)}")
    suite: SuiteSpec = suites[suite_name]
    keys = suite.tasks
    if only_tasks:
        unknown = sorted(set(only_tasks) - set(suite.tasks))
        if unknown:
            raise KeyError(f"tasks not in suite {suite_name}: {unknown}")
        keys = [key for key in suite.tasks if key in set(only_tasks)]
    selected = select_tasks(repo_root / "tasks", keys)
    require_graders(selected)
    scientific = is_scientific_source(source)
    if tracks:
        chosen_tracks = tracks
    elif track:
        chosen_tracks = [track]
    elif suite.tracks:
        chosen_tracks = list(suite.tracks)
    else:
        chosen_tracks = [suite.track or PRIMARY_TRACK]
    if models:
        chosen_models = models
    elif model:
        chosen_models = [model]
    elif chosen_tracks == [CLAUDE_CODE_TRACK]:
        # Suite model lists name the API series; the product series has its own model.
        chosen_models = [CLAUDE_CODE_MODEL]
    elif chosen_tracks == [CURSOR_TRACK] and not suite.models:
        chosen_models = [CURSOR_MODEL]
    elif suite.models:
        chosen_models = list(suite.models)
    elif any(item in CANONICAL_TRACKS for item in chosen_tracks):
        chosen_models = list(CANONICAL_MODELS)
    else:
        chosen_models = [REQUEST_MODEL]
    efforts = efforts or suite.efforts or list(CANONICAL_EFFORTS)
    repeats = repeats or suite.trials
    for chosen_track in chosen_tracks:
        if chosen_track == ULTRA_TRACK:
            continue
        for effort in efforts:
            if effort == "ultra":
                raise ValueError("Ultra cannot be mixed into a single-agent evaluate; use --track codex_ultra")
            if chosen_track == PRIMARY_TRACK:
                assert_single_agent_effort(effort)
            for chosen_model in chosen_models:
                if chosen_track in {*CANONICAL_TRACKS, CLAUDE_CODE_TRACK, CURSOR_TRACK}:
                    validate_model_effort_track(chosen_model, effort, chosen_track)
    store = Store(database_url or "sqlite:///./artifacts/regression.db")
    artifacts = ArtifactStore(artifact_dir or repo_root / "artifacts" / "runs")
    hashed = suite_hash(selected, suite.name, suite.version)
    tasks_by_id = {task.id: task for task in selected}
    schedule = build_schedule([task.id for task in selected], efforts, repeats, run_seed)

    run_ids: dict[str, str] = {}
    coordinators: dict[tuple[str, str, str], tuple[Coordinator, str, ModelConfig]] = {}

    for chosen_track in chosen_tracks:
        for effort in efforts:
            for chosen_model in chosen_models:
                config = _config_for(
                    track=chosen_track,
                    model=chosen_model,
                    effort=effort,
                    client_mode=client_mode,
                )
                provider = None
                capability = None
                if source in {"model", "fake"}:
                    provider, capability = make_provider(
                        config,
                        source=source,
                        fake_scenario=fake_scenario,
                    )
                coordinator = Coordinator(store, artifacts, provider, repo_root)
                suite_id, _ = coordinator.ensure_suite_and_task(suite.name, suite.version, selected[0])
                config_id = coordinator.ensure_config(config, config.toolset_version, config.system_prompt_version)
                environment = collect_environment_manifest(
                    root=repo_root,
                    track=config.track,
                    provider=config.provider,
                    requested_model=config.model,
                    verified_model=capability.verified_model if capability else None,
                    requested_effort=config.reasoning_effort,
                    verified_effort=capability.verified_effort if capability else None,
                    auth_surface=config.auth_surface,
                    client_mode=client_mode,
                    suite_id=suite.name,
                    suite_version=suite.version,
                    suite_hash=hashed,
                    schedule_seed=run_seed,
                    extra={
                        "source": source,
                        "fresh": fresh,
                        "workers": workers,
                        "scientific_data": scientific,
                        "blocked_randomization": blocked_randomization,
                        "bootstrap_seed": bootstrap_seed,
                        "fixture_seed": fixture_seed,
                        "grader_seed": grader_seed,
                    },
                )
                run_id = store.create_run(
                    {
                        "suite_id": suite_id,
                        "config_id": config_id,
                        "trigger": trigger,
                        "status": "running",
                        "runner_git_sha": runner_git_sha(repo_root),
                        "environment": environment,
                        "notes": f"{suite_name} {chosen_model} {effort} {source} {chosen_track}",
                        "track": config.track,
                        "client_mode": client_mode,
                        "schedule_seed": run_seed,
                        "suite_hash": hashed,
                        "requested_model": config.model,
                        "verified_model": capability.verified_model if capability else None,
                        "requested_effort": config.reasoning_effort,
                        "verified_effort": capability.verified_effort if capability else None,
                        "auth_surface": config.auth_surface,
                        "source": source,
                        "scientific_data": scientific,
                        "protocol_version": config.protocol_version,
                        "tool_protocol_version": config.tool_protocol_version,
                        "bootstrap_seed": bootstrap_seed,
                        "fixture_seed": fixture_seed,
                        "grader_seed": grader_seed,
                    }
                )
                store.put_environment_manifest(run_id, environment, canonical_hash(environment))
                coordinators[(chosen_model, effort, chosen_track)] = (coordinator, run_id, config)
                run_ids[f"{chosen_model}/{effort}/{chosen_track}"] = run_id
                print(
                    f"[evaluate] suite={suite_name} model={chosen_model} effort={effort} "
                    f"track={config.track} source={source} scientific_data={scientific} "
                    f"run_id={run_id} seed={run_seed}",
                    flush=True,
                )

    jobs: list[tuple[Any, ...]] = []
    for chosen_track in chosen_tracks:
        for item in schedule:
            models_for_slot = list(chosen_models)
            key = pair_key(
                suite_hash=hashed,
                task_id=item.task_id,
                task_version=tasks_by_id[item.task_id].version,
                track=chosen_track,
                effort=item.effort,
                repeat_index=item.trial_index,
                schedule_seed=run_seed,
            )
            if blocked_randomization:
                models_for_slot = blocked_model_order(
                    models_for_slot,
                    pair_key_value=key,
                    schedule_seed=run_seed,
                )
            for chosen_model in models_for_slot:
                coordinator, run_id, config = coordinators[(chosen_model, item.effort, chosen_track)]
                task = tasks_by_id[item.task_id]
                _, task_id = coordinator.ensure_suite_and_task(suite.name, suite.version, task)
                jobs.append((chosen_track, item, key, chosen_model, coordinator, run_id, config, task, task_id))

    lock = threading.Lock()
    failures = 0

    def run_job(job: tuple[Any, ...]) -> None:
        nonlocal failures
        chosen_track, item, key, chosen_model, coordinator, run_id, config, task, task_id = job
        outcome = coordinator.run_attempt(
            run_id=run_id,
            suite_version=suite.version,
            task=task,
            task_version_id=task_id,
            config=config,
            trial_index=item.trial_index,
            source=source,  # type: ignore[arg-type]
            schedule_order=item.order,
            expected_hashes=task.computed_hashes() if not fresh else None,
            pair_key=key,
            schedule_seed=run_seed,
            bootstrap_seed=bootstrap_seed,
            fixture_seed=fixture_seed,
            grader_seed=grader_seed,
        )
        grade = outcome.grade
        with lock:
            print(
                f"  pair={key} model={chosen_model} {task.id} trial={item.trial_index} "
                f"track={chosen_track} effort={item.effort} "
                f"status={outcome.quality_status} "
                f"strict_pass={None if grade is None else grade.strict_pass} "
                f"error={outcome.error_code}",
                flush=True,
            )
            if outcome.quality_status == "harness_fail":
                failures += 1

    if workers <= 1:
        for job in jobs:
            run_job(job)
    else:
        # Jobs start in schedule order; with N workers up to N attempts run at once.
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for future in [pool.submit(run_job, job) for job in jobs]:
                future.result()

    for _key, (_coordinator, run_id, _config) in coordinators.items():
        store.complete_run(run_id, "completed" if failures == 0 else "completed_with_errors")
    return run_ids


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a Grok / Astra longitudinal evaluation")
    parser.add_argument("--suite", default="canary")
    parser.add_argument("--model", default="")
    parser.add_argument("--models", default="")
    parser.add_argument("--efforts", default=",".join(CANONICAL_EFFORTS))
    parser.add_argument("--track", default="")
    parser.add_argument("--tracks", default="")
    parser.add_argument("--client-mode", default="latest", choices=["pinned", "latest"])
    parser.add_argument("--repeats", type=int)
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--artifact-dir", default="artifacts/runs")
    parser.add_argument("--source", choices=["model", "gold", "none", "fake"], default="model")
    parser.add_argument("--trigger", default="cli")
    parser.add_argument("--seed", type=int, default=20260911)
    parser.add_argument("--schedule-seed", type=int)
    parser.add_argument("--bootstrap-seed", type=int, default=20260911)
    parser.add_argument("--fixture-seed", type=int, default=20260911)
    parser.add_argument("--grader-seed", type=int, default=20260911)
    parser.add_argument("--blocked-randomization", action="store_true")
    parser.add_argument("--fake-scenario", default="ok")
    parser.add_argument("--tasks", default="", help="comma-separated subset of the suite's tasks")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path.cwd()
    models = _parse_csv(args.models, [])
    if args.model:
        models = [args.model] if not models else models
    tracks = _parse_csv(args.tracks, [])
    track = args.track or None
    # With no flags the suite's own tracks/models apply (see evaluate()).
    run_ids = evaluate(
        repo_root,
        args.suite,
        efforts=[item.strip() for item in args.efforts.split(",") if item.strip()],
        source=args.source,
        workers=args.workers,
        artifact_dir=Path(args.artifact_dir),
        trigger=args.trigger,
        track=track,
        tracks=tracks or None,
        model=None,
        models=models or None,
        repeats=args.repeats,
        client_mode=args.client_mode,
        fresh=args.fresh,
        run_seed=args.schedule_seed if args.schedule_seed is not None else args.seed,
        blocked_randomization=args.blocked_randomization,
        bootstrap_seed=args.bootstrap_seed,
        fixture_seed=args.fixture_seed,
        grader_seed=args.grader_seed,
        fake_scenario=args.fake_scenario,
        only_tasks=_parse_csv(args.tasks, []) or None,
    )
    print(json.dumps({"run_ids": run_ids}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
