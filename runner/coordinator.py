"""Evaluation coordinator: Codex runs first; private graders run only after exit."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from runner import PRIMARY_TRACK, REQUEST_MODEL
from runner.artifacts import ArtifactStore
from runner.config import ModelConfig
from runner.errors import HarnessError, InfrastructureError, InvalidConfigurationError
from runner.grader import Grade, grade_workspace
from runner.hash_tree import canonical_hash, hash_text, hash_tree
from runner.images import host_execution_allowed, resolve_environment, task_requirements
from runner.isolation import assert_agent_namespace_clean, workspace_leak_report
from runner.patch import PatchError, git_apply, workspace_diff
from runner.providers.base import Provider, ProviderResult
from runner.protocols import tool_protocol_version
from runner.sandbox import TaskSandbox
from runner.scientific import is_scientific_source
from runner.status import classify_attempt
from runner.storage import Store, utcnow
from runner.tasks import TaskSpec
from runner.tracks import run_agentic, run_model_only

Source = Literal["model", "gold", "none", "negative"]


def runner_git_sha(root: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return proc.stdout.strip()
    return "unknown"


def logical_attempt_key(
    suite_version: str,
    task_version: str,
    model_config_hash: str,
    trial_index: int,
    run_id: str,
) -> str:
    return f"{suite_version}:{task_version}:{model_config_hash}:{trial_index}:{run_id}"


@dataclass
class AttemptOutcome:
    attempt_id: str
    status: str
    quality_status: str
    grade: Grade | None
    error_code: str | None = None
    error: dict[str, Any] | None = None
    provider_result: ProviderResult | None = None


class Coordinator:
    def __init__(
        self,
        store: Store,
        artifacts: ArtifactStore,
        provider: Provider | None,
        repo_root: Path,
        daily_budget_usd: float = 150.0,
        max_attempt_cost_usd: float = 5.0,
        infra_retry_policy: dict[str, Any] | None = None,
    ) -> None:
        self.store = store
        self.artifacts = artifacts
        self.provider = provider
        self.repo_root = repo_root
        self.daily_budget_usd = daily_budget_usd
        self.max_attempt_cost_usd = max_attempt_cost_usd
        self.infra_retry_policy = infra_retry_policy or {"max_retries": 0, "retry_on": ["http_429", "http_5xx", "network"]}

    def ensure_suite_and_task(self, suite_name: str, suite_version: str, task: TaskSpec) -> tuple[str, str]:
        hashes = task.computed_hashes()
        suite_id = self.store.upsert_suite(
            name=suite_name,
            version=suite_version,
            git_sha=runner_git_sha(self.repo_root),
            manifest_sha256=canonical_hash(
                {"name": suite_name, "version": suite_version}
            ),
        )
        task_id = self.store.upsert_task(
            suite_id,
            {
                "task_key": task.id,
                "task_version": task.version,
                "category": task.family,
                "language": (task.manifest.get("languages") or [task.manifest.get("language")])[0],
                "difficulty": task.manifest.get("difficulty"),
                "prompt_sha256": hashes["prompt_sha256"],
                "fixture_sha256": hashes["fixture_sha256"],
                "grader_sha256": hashes["grader_sha256"],
                "container_image_digest": task.manifest.get("environment", {}).get(
                    "image", "local-fallback"
                ),
                "context_class": task.manifest.get("context_class")
                or task.manifest.get("tags", [None])[0],
                "manifest": task.manifest,
            },
        )
        return suite_id, task_id

    def ensure_config(self, config: ModelConfig, tools_sha: str, system_sha: str) -> str:
        return self.store.upsert_model_config(
            {
                "provider": config.provider,
                "request_model": config.model,
                "reasoning_effort": config.reasoning_effort,
                "temperature": config.temperature,
                "top_p": config.top_p,
                "max_output_tokens": config.max_output_tokens,
                "system_prompt_sha256": system_sha,
                "tools_sha256": tools_sha,
                "config": config.to_canonical(),
                "config_sha256": config.config_sha256,
            }
        )

    def run_attempt(
        self,
        *,
        run_id: str,
        suite_version: str,
        task: TaskSpec,
        task_version_id: str,
        config: ModelConfig,
        trial_index: int,
        source: Source = "model",
        work_root: Path | None = None,
        schedule_order: int | None = None,
        negative_name: str | None = None,
        expected_hashes: dict[str, str] | None = None,
        pair_key: str | None = None,
        schedule_seed: int | None = None,
        bootstrap_seed: int | None = None,
        fixture_seed: int | None = None,
        grader_seed: int | None = None,
    ) -> AttemptOutcome:
        key = logical_attempt_key(
            suite_version,
            f"{task.id}@{task.version}",
            config.config_sha256,
            trial_index,
            run_id,
        )
        attempt_id = self.store.create_attempt(
            {
                "run_id": run_id,
                "task_version_id": task_version_id,
                "trial_index": trial_index,
                "logical_attempt_key": key,
                "requested_model": config.model,
                "requested_effort": config.reasoning_effort,
                "track": config.track,
                "client_mode": getattr(config, "client_mode", "latest"),
                "auth_surface": getattr(config, "auth_surface", None) or (
                    "chatgpt" if config.track in {PRIMARY_TRACK, "codex_ultra"} else "api_key"
                ),
                "status": "running",
                "started_at": utcnow(),
                "schedule_order": schedule_order,
                "pair_key": pair_key,
                "protocol_version": getattr(config, "protocol_version", "1.0.0"),
                "tool_protocol_version": getattr(config, "tool_protocol_version", None)
                or tool_protocol_version(),
                "scientific_data": is_scientific_source(source),
                "schedule_seed": schedule_seed,
                "bootstrap_seed": bootstrap_seed,
                "fixture_seed": fixture_seed,
                "grader_seed": grader_seed,
                "source": source,
            }
        )
        seq = 0
        sandbox: TaskSandbox | None = None
        started = time.perf_counter()
        try:
            hashes = task.computed_hashes()
            if expected_hashes:
                for name in ("fixture_sha256", "grader_sha256"):
                    if expected_hashes.get(name) and expected_hashes[name] != hashes.get(name):
                        raise InvalidConfigurationError(
                            f"task mutation detected for {task.id}: {name} changed"
                        )

            env = task.manifest.get("environment", {})
            network = bool(env.get("network", False))
            try:
                requirements = task_requirements(task.manifest, task.root)
            except ValueError as exc:
                raise InvalidConfigurationError(f"task mutation detected: {exc}") from exc
            requirements_sha = requirements[1] if requirements else None
            sandbox = TaskSandbox(
                requirements_sha256=requirements_sha,
                fixture=task.fixture_path,
                work_root=work_root,
                image=env.get("image"),
                network=network,
                env=env.get("env") or {},
                verify_sha256=task.manifest.get("fixture", {}).get("sha256"),
                materialize_commands=task.manifest.get("fixture", {}).get("materialize") or [],
            )
            sandbox.materialize()
            leaks = workspace_leak_report(sandbox.workspace, task.grader_path)
            if leaks:
                raise HarnessError("workspace leaked private grader material: " + "; ".join(leaks))
            assert_agent_namespace_clean(sandbox.workspace, task.grader_path)
            if is_scientific_source(source) and not sandbox.uses_docker() and not host_execution_allowed():
                # Checked before the agent runs so no subscription usage is spent.
                build = "build-env" if requirements_sha else "build"
                raise InvalidConfigurationError(
                    f"no container image for task image {env.get('image')!r}: run `python -m runner images {build}` "
                    "(or set LLMREG_ALLOW_HOST=1 to grade on the host)"
                )
            agent_image = None
            if requirements_sha and source == "model" and getattr(self.provider, "runtime", None) is not None:
                # The agent works in the same dependencies the grader tests with.
                kind = getattr(self.provider, "environment_kind", "agent")
                agent_image = resolve_environment(requirements_sha, kind)
                if agent_image is None:
                    raise InvalidConfigurationError(
                        f"no agent environment image for {task.id}: run `python -m runner images build-env`"
                    )
            self.store.add_event(
                attempt_id,
                seq,
                "workspace_ready",
                {"workspace": str(sandbox.workspace), "fixture_sha256": hash_tree(sandbox.workspace)},
            )
            seq += 1

            grade: Grade | None = None
            provider_result: ProviderResult | None = None
            usage: dict[str, Any] = {}
            retry_count = 0
            api_latency_ms = None
            response_sha = None
            diff_text = ""
            quality_status = None
            error_code = None
            error: dict[str, Any] | None = None

            if source == "gold":
                diff_text = task.gold_patch.read_text(encoding="utf-8")
                git_apply(sandbox.workspace, diff_text)
                self.store.add_event(attempt_id, seq, "gold_applied", {"bytes": len(diff_text)})
                seq += 1
            elif source == "negative":
                if not negative_name:
                    raise HarnessError("negative source requires negative_name")
                patch_path = task.negatives_dir / negative_name
                diff_text = patch_path.read_text(encoding="utf-8")
                if diff_text.strip():
                    git_apply(sandbox.workspace, diff_text)
                self.store.add_event(attempt_id, seq, "negative_applied", {"name": negative_name})
                seq += 1
            elif source == "none":
                self.store.add_event(attempt_id, seq, "broken_fixture", {})
                seq += 1
            elif source == "model":
                if self.provider is None:
                    raise InfrastructureError("provider is not configured")
                prompt = task.prompt_text()
                timeout = int(task.manifest.get("budgets", {}).get("task_wall_seconds", 900))
                max_tools = int(task.manifest.get("budgets", {}).get("max_tool_calls", 80))
                if config.track == "model_only":
                    provider_result = run_model_only(
                        provider=self.provider,
                        prompt=prompt,
                        workspace=sandbox.workspace,
                        model=config.model,
                        effort=config.reasoning_effort,
                        timeout_seconds=timeout,
                    )
                elif config.track == "agentic":
                    provider_result = run_agentic(
                        provider=self.provider,
                        prompt=prompt,
                        workspace=sandbox.workspace,
                        model=config.model,
                        effort=config.reasoning_effort,
                        timeout_seconds=timeout,
                        max_tool_calls=max_tools,
                    )
                else:
                    provider_result = self.provider.run_attempt(
                        prompt=prompt,
                        workspace=sandbox.workspace,
                        model=config.model,
                        effort=config.reasoning_effort,
                        timeout_seconds=timeout,
                        **({"image": agent_image} if agent_image else {}),
                    )
                retry_count = provider_result.retry_count
                api_latency_ms = provider_result.latency_ms
                usage = provider_result.usage or {}
                response_sha = hash_text(provider_result.raw_jsonl or provider_result.final_text or "")
                self._write_attempt_artifacts(run_id, attempt_id, provider_result, prompt)
                for index, event in enumerate(provider_result.events):
                    self.store.add_event(
                        attempt_id,
                        seq,
                        str(event.get("type") or "codex_event"),
                        event if isinstance(event, dict) else {"value": event},
                    )
                    seq += 1
                    if index > 500:
                        break
                if provider_result.invalid_configuration:
                    quality_status = classify_attempt(
                        invalid_configuration=True,
                        error_code=provider_result.error_code,
                    )
                    error_code = provider_result.error_code or "invalid_configuration"
                    error = provider_result.error
                elif provider_result.infrastructure:
                    quality_status = classify_attempt(
                        infrastructure=True,
                        error_code=provider_result.error_code,
                    )
                    error_code = provider_result.error_code or "infrastructure"
                    error = provider_result.error
                elif provider_result.error_code == "timeout":
                    quality_status = classify_attempt(timed_out=True, error_code="timeout")
                    error_code = "timeout"
                    error = provider_result.error
            else:
                raise ValueError(f"Unknown source: {source}")

            if quality_status in {"infra_fail", "invalid_configuration", "harness_fail"}:
                self._finish_without_grade(
                    attempt_id,
                    provider_result,
                    quality_status,
                    error_code,
                    error,
                    started,
                    usage,
                    retry_count,
                    api_latency_ms,
                    response_sha,
                    config,
                )
                return AttemptOutcome(
                    attempt_id,
                    quality_status,
                    quality_status,
                    None,
                    error_code,
                    error,
                    provider_result,
                )

            if not diff_text and source != "none":
                diff_text = workspace_diff(sandbox.workspace)
            diff_art = self.artifacts.write_text(f"{run_id}/{attempt_id}/final.patch", diff_text)
            manifest_art = self.artifacts.write_json(
                f"{run_id}/{attempt_id}/workspace_manifest.json",
                {
                    "files": sorted(
                        path.relative_to(sandbox.workspace).as_posix()
                        for path in sandbox.workspace.rglob("*")
                        if path.is_file() and ".git" not in path.parts
                    )
                },
            )
            self.store.add_artifact(attempt_id, "final.patch", diff_art["uri"], diff_art["sha256"])
            self.store.add_artifact(
                attempt_id, "workspace_manifest.json", manifest_art["uri"], manifest_art["sha256"]
            )

            # Grade only after the agent process has exited. Mount the private
            # grader into a side directory that the finished workspace cannot
            # have enumerated during execution.
            sandbox.mount_grader(task.grader_path)
            grader_spec = task.manifest.get("grader", {})
            grade = grade_workspace(
                sandbox,
                list(grader_spec.get("command") or ["bash", "/grader/run.sh"]),
                int(grader_spec.get("timeout_seconds", 120)),
                fixture=task.fixture_path,
                forbidden_paths=list(task.manifest.get("permissions", {}).get("forbidden_paths") or []),
                weights={
                    name: float(group.get("weight", 0))
                    for name, group in (task.manifest.get("scoring", {}).get("groups") or {}).items()
                },
                required=list(task.manifest.get("scoring", {}).get("strict_pass_requires") or []),
            )
            grader_json = self.artifacts.write_json(
                f"{run_id}/{attempt_id}/grader.json",
                {
                    "strict_pass": grade.strict_pass,
                    "functional": grade.functional_score,
                    "regression": grade.regression_score,
                    "constraints": grade.constraints_score,
                    "details": grade.details,
                    "tests": grade.test_results,
                },
            )
            grader_log = self.artifacts.write_text(
                f"{run_id}/{attempt_id}/grader.log",
                (grade.raw_stdout or "") + "\n--- stderr ---\n" + (grade.raw_stderr or ""),
            )
            self.store.add_artifact(attempt_id, "grader.json", grader_json["uri"], grader_json["sha256"])
            self.store.add_artifact(attempt_id, "grader.log", grader_log["uri"], grader_log["sha256"])

            quality_status = classify_attempt(
                strict_pass=grade.strict_pass,
                timed_out=bool(provider_result and provider_result.error_code == "timeout"),
            )
            self.store.add_score(
                {
                    "attempt_id": attempt_id,
                    "strict_pass": grade.strict_pass,
                    "functional_score": grade.functional_score,
                    "regression_score": grade.regression_score,
                    "constraints_score": grade.constraints_score,
                    "partial_score": grade.partial_score,
                    "tests_passed": grade.tests_passed,
                    "tests_total": grade.tests_total,
                    "forbidden_change_count": grade.forbidden_change_count,
                    "grading_details": grade.details,
                }
            )
            seen_cases: set[str] = set()
            for idx, item in enumerate(grade.test_results):
                case_id = str(item.get("id") or item.get("name") or f"{item.get('group', 'test')}-{idx}")
                if case_id in seen_cases:
                    case_id = f"{case_id}:{idx}"
                seen_cases.add(case_id)
                self.store.add_test_result(
                    {
                        "attempt_id": attempt_id,
                        "test_case_id": case_id,
                        "test_group": item.get("group", "functional"),
                        "passed": item.get("passed", False),
                        "weight": item.get("weight", 1),
                        "failure_signature": item.get("failure_signature"),
                        "details": item,
                    }
                )
            self._update_success(
                attempt_id,
                provider_result,
                quality_status,
                grade,
                started,
                usage,
                retry_count,
                api_latency_ms,
                response_sha,
                diff_art,
                config,
                error_code=error_code,
                error=error,
            )
            return AttemptOutcome(attempt_id, quality_status, quality_status, grade, error_code, error, provider_result)
        except InvalidConfigurationError as exc:
            quality_status = "invalid_configuration"
            self.store.update_attempt(
                attempt_id,
                {
                    "status": quality_status,
                    "quality_status": quality_status,
                    "completed_at": utcnow(),
                    "error_code": "invalid_configuration",
                    "error": {"message": str(exc), "requested": exc.requested, "verified": exc.verified},
                    "wall_time_ms": int((time.perf_counter() - started) * 1000),
                },
            )
            return AttemptOutcome(
                attempt_id, quality_status, quality_status, None, "invalid_configuration", {"message": str(exc)}
            )
        except InfrastructureError as exc:
            quality_status = "infra_fail"
            self.store.update_attempt(
                attempt_id,
                {
                    "status": quality_status,
                    "quality_status": quality_status,
                    "completed_at": utcnow(),
                    "error_code": "infrastructure",
                    "error": {"message": str(exc), "status": getattr(exc, "status", None)},
                    "wall_time_ms": int((time.perf_counter() - started) * 1000),
                },
            )
            return AttemptOutcome(
                attempt_id, quality_status, quality_status, None, "infrastructure", {"message": str(exc)}
            )
        except PatchError as exc:
            quality_status = "quality_fail"
            self.store.update_attempt(
                attempt_id,
                {
                    "status": quality_status,
                    "quality_status": quality_status,
                    "completed_at": utcnow(),
                    "error_code": "invalid_patch",
                    "error": {"message": str(exc)},
                    "wall_time_ms": int((time.perf_counter() - started) * 1000),
                },
            )
            self.store.add_score(
                {
                    "attempt_id": attempt_id,
                    "strict_pass": False,
                    "functional_score": 0,
                    "regression_score": 0,
                    "constraints_score": 0,
                    "partial_score": 0,
                    "tests_passed": 0,
                    "tests_total": 0,
                    "forbidden_change_count": 0,
                    "grading_details": {"error": "invalid_patch", "message": str(exc)},
                }
            )
            return AttemptOutcome(
                attempt_id, quality_status, quality_status, None, "invalid_patch", {"message": str(exc)}
            )
        except HarnessError as exc:
            quality_status = "harness_fail"
            self.store.update_attempt(
                attempt_id,
                {
                    "status": quality_status,
                    "quality_status": quality_status,
                    "completed_at": utcnow(),
                    "error_code": "harness",
                    "error": {"message": str(exc)},
                    "wall_time_ms": int((time.perf_counter() - started) * 1000),
                },
            )
            return AttemptOutcome(attempt_id, quality_status, quality_status, None, "harness", {"message": str(exc)})
        except Exception as exc:  # noqa: BLE001
            quality_status = "harness_fail"
            self.store.update_attempt(
                attempt_id,
                {
                    "status": quality_status,
                    "quality_status": quality_status,
                    "completed_at": utcnow(),
                    "error_code": type(exc).__name__,
                    "error": {"message": str(exc)},
                    "wall_time_ms": int((time.perf_counter() - started) * 1000),
                },
            )
            return AttemptOutcome(
                attempt_id, quality_status, quality_status, None, type(exc).__name__, {"message": str(exc)}
            )
        finally:
            if sandbox is not None:
                sandbox.cleanup()

    def _write_attempt_artifacts(
        self,
        run_id: str,
        attempt_id: str,
        result: ProviderResult,
        prompt: str,
    ) -> None:
        written = [
            self.artifacts.write_text(f"{run_id}/{attempt_id}/raw_codex.jsonl", result.raw_jsonl or ""),
            self.artifacts.write_text(f"{run_id}/{attempt_id}/stdout.log", result.stdout or ""),
            self.artifacts.write_text(f"{run_id}/{attempt_id}/stderr.log", result.stderr or ""),
            self.artifacts.write_json(
                f"{run_id}/{attempt_id}/manifest.json",
                {
                    "command": result.command,
                    "requested_model": result.requested_model,
                    "verified_model": result.verified_model,
                    "requested_effort": result.requested_effort,
                    "verified_effort": result.verified_effort,
                    "auth_surface": result.auth_surface,
                    "thread_id": result.thread_id,
                    "usage": result.usage,
                    "prompt": prompt,
                    "exit_code": result.exit_code,
                },
            ),
        ]
        kinds = ["raw_codex.jsonl", "stdout.log", "stderr.log", "manifest.json"]
        for kind, artifact in zip(kinds, written, strict=True):
            self.store.add_artifact(attempt_id, kind, artifact["uri"], artifact["sha256"])

    def _finish_without_grade(
        self,
        attempt_id: str,
        provider_result: ProviderResult | None,
        quality_status: str,
        error_code: str | None,
        error: dict[str, Any] | None,
        started: float,
        usage: dict[str, Any],
        retry_count: int,
        api_latency_ms: float | None,
        response_sha: str | None,
        config: ModelConfig,
    ) -> None:
        details_in = usage.get("input_tokens_details") or {}
        details_out = usage.get("output_tokens_details") or {}
        self.store.update_attempt(
            attempt_id,
            {
                "status": quality_status,
                "quality_status": quality_status,
                "completed_at": utcnow(),
                "resolved_model": provider_result.verified_model if provider_result else None,
                "verified_model": provider_result.verified_model if provider_result else None,
                "verified_effort": provider_result.verified_effort if provider_result else None,
                "requested_effort": config.reasoning_effort,
                "api_response_id": provider_result.thread_id if provider_result else None,
                "thread_id": provider_result.thread_id if provider_result else None,
                "retry_count": retry_count,
                "api_latency_ms": int(api_latency_ms) if api_latency_ms is not None else None,
                "wall_time_ms": int((time.perf_counter() - started) * 1000),
                "input_tokens": usage.get("input_tokens"),
                "cached_input_tokens": details_in.get(
                    "cached_tokens", usage.get("cached_input_tokens", usage.get("cache_read_input_tokens"))
                ),
                "output_tokens": usage.get("output_tokens"),
                "reasoning_tokens": details_out.get("reasoning_tokens", usage.get("reasoning_tokens")),
                "total_tokens": usage.get("total_tokens"),
                "cost_usd_ticks": cost_usd_ticks(usage),
                "response_sha256": response_sha,
                "exit_code": provider_result.exit_code if provider_result else None,
                "error_code": error_code,
                "error": error,
            },
        )

    def _update_success(
        self,
        attempt_id: str,
        provider_result: ProviderResult | None,
        quality_status: str,
        grade: Grade,
        started: float,
        usage: dict[str, Any],
        retry_count: int,
        api_latency_ms: float | None,
        response_sha: str | None,
        diff_art: dict[str, str],
        config: ModelConfig,
        error_code: str | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        details_in = usage.get("input_tokens_details") or {}
        details_out = usage.get("output_tokens_details") or {}
        self.store.update_attempt(
            attempt_id,
            {
                "status": quality_status,
                "quality_status": quality_status,
                "completed_at": utcnow(),
                "resolved_model": provider_result.verified_model if provider_result else None,
                "verified_model": provider_result.verified_model if provider_result else None,
                "verified_effort": provider_result.verified_effort if provider_result else None,
                "requested_effort": config.reasoning_effort,
                "api_response_id": provider_result.thread_id if provider_result else None,
                "thread_id": provider_result.thread_id if provider_result else None,
                "retry_count": retry_count,
                "api_latency_ms": int(api_latency_ms) if api_latency_ms is not None else None,
                "wall_time_ms": int((time.perf_counter() - started) * 1000),
                "grader_time_ms": int(grade.duration_ms),
                "input_tokens": usage.get("input_tokens"),
                "cached_input_tokens": details_in.get(
                    "cached_tokens", usage.get("cached_input_tokens", usage.get("cache_read_input_tokens"))
                ),
                "output_tokens": usage.get("output_tokens"),
                "reasoning_tokens": details_out.get("reasoning_tokens", usage.get("reasoning_tokens")),
                "total_tokens": usage.get("total_tokens"),
                "cost_usd_ticks": cost_usd_ticks(usage),
                "response_sha256": response_sha,
                "workspace_diff_sha256": diff_art["sha256"],
                "diff_artifact_uri": diff_art["uri"],
                "exit_code": grade.returncode,
                # A graded attempt can still carry the provider's error: a timeout fails it even when
                # the change it left behind passes the hidden tests.
                "error_code": error_code,
                "error": error,
            },
        )


def cost_usd_ticks(usage: dict[str, Any]) -> int | None:
    """Attempt cost in USD ticks (1e-10 USD) from provider usage, when reported."""
    for key in ("cost_usd_ticks", "cost_in_usd_ticks"):
        if usage.get(key) is not None:
            return int(usage[key])
    if usage.get("equivalent_cost_usd") is not None:
        # Subscription products report an API-equivalent cost, not a charge.
        return round(float(usage["equivalent_cost_usd"]) * 10_000_000_000)
    return None


def requested_model() -> str:
    return os.environ.get("SOL_REGRESSION_MODEL", REQUEST_MODEL)
