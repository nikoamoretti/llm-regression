from __future__ import annotations

import pytest

from runner.baselines import compare_identities, create_baseline, lock_baseline, show_baseline
from runner.identity import SeriesIdentity, require_compatible
from runner.storage import Store


def _identity(**overrides) -> SeriesIdentity:
    data = dict(
        track="codex_product",
        model="gpt-5.6-sol",
        effort="max",
        client_mode="latest",
        suite_id="canary",
        suite_version="v1",
        suite_hash="abc",
        grading_protocol="hidden_final_state",
        auth_surface="chatgpt",
    )
    data.update(overrides)
    return SeriesIdentity(**data)


def test_ultra_cannot_mix_with_max() -> None:
    with pytest.raises(ValueError, match="Ultra"):
        require_compatible(_identity(effort="max"), _identity(effort="ultra", track="codex_ultra"))


def test_tracks_cannot_mix() -> None:
    with pytest.raises(ValueError, match="track"):
        require_compatible(_identity(), _identity(track="responses_control", auth_surface="api_key"))


def test_client_modes_cannot_mix() -> None:
    with pytest.raises(ValueError, match="pinned"):
        require_compatible(_identity(), _identity(client_mode="pinned"))


def test_force_is_non_canonical() -> None:
    result = require_compatible(_identity(), _identity(track="responses_control", auth_surface="api_key"), force=True)
    assert result["non_canonical"] is True


def test_baseline_lock_is_immutable() -> None:
    store = Store("sqlite://")
    suite = store.upsert_suite(name="canary", version="v1", git_sha="x", manifest_sha256="h")
    cfg = store.upsert_model_config(
        {
            "provider": "codex_cli",
            "request_model": "gpt-5.6-sol",
            "reasoning_effort": "max",
            "config": {},
            "config_sha256": "c",
        }
    )
    run = store.create_run({"suite_id": suite, "config_id": cfg, "trigger": "t", "status": "completed", "runner_git_sha": "x"})
    baseline_id = create_baseline(
        store,
        name="sol-max-baseline",
        run_ids=[run],
        track="codex_product",
        model="gpt-5.6-sol",
        effort="max",
        suite_id="canary",
        suite_version="v1",
        suite_hash="abc",
    )
    lock_baseline(store, baseline_id)
    with pytest.raises(ValueError, match="locked"):
        store.update_baseline(baseline_id, {"notes": "nope"})
    locked = show_baseline(store, "sol-max-baseline")
    assert locked["locked"] in {1, True}


def test_task_mutation_after_baseline_lock_is_rejected() -> None:
    store = Store("sqlite://")
    suite = store.upsert_suite(name="canary", version="v1", git_sha="x", manifest_sha256="h")
    cfg = store.upsert_model_config(
        {
            "provider": "codex_cli",
            "request_model": "gpt-5.6-sol",
            "reasoning_effort": "max",
            "config": {},
            "config_sha256": "c-lock",
        }
    )
    run = store.create_run(
        {"suite_id": suite, "config_id": cfg, "trigger": "t", "status": "completed", "runner_git_sha": "x"}
    )
    baseline_id = create_baseline(
        store,
        name="sol-max-locked",
        run_ids=[run],
        track="codex_product",
        model="gpt-5.6-sol",
        effort="max",
        suite_id="canary",
        suite_version="v1",
        suite_hash="frozen-v1-hash",
    )
    lock_baseline(store, baseline_id)
    locked = show_baseline(store, "sol-max-locked")
    mutated = {
        "track": "codex_product",
        "model": "gpt-5.6-sol",
        "effort": "max",
        "client_mode": "latest",
        "suite_id": "canary",
        "suite_version": "v1",
        "suite_hash": "mutated-after-lock",
        "grading_protocol": "hidden_final_state",
        "auth_surface": "chatgpt",
    }
    with pytest.raises(ValueError, match="hash"):
        compare_identities(locked, mutated)


def test_grok_and_astra_cannot_mix() -> None:
    with pytest.raises(ValueError, match="model"):
        require_compatible(
            _identity(track="agentic", model="grok-4.6", effort="xhigh", auth_surface="api_key"),
            _identity(track="agentic", model="gpt-6-astra", effort="xhigh", auth_surface="api_key"),
        )


def test_model_only_and_agentic_cannot_mix() -> None:
    with pytest.raises(ValueError, match="track"):
        require_compatible(
            _identity(track="model_only", model="grok-4.6", effort="xhigh", auth_surface="api_key"),
            _identity(track="agentic", model="grok-4.6", effort="xhigh", auth_surface="api_key"),
        )


def test_astra_max_cannot_mix_with_xhigh() -> None:
    with pytest.raises(ValueError, match="max"):
        require_compatible(
            _identity(track="agentic", model="gpt-6-astra", effort="xhigh", auth_surface="api_key"),
            _identity(track="agentic", model="gpt-6-astra", effort="max", auth_surface="api_key"),
        )


def test_codex_cannot_mix_with_api_track() -> None:
    with pytest.raises(ValueError, match="Codex"):
        require_compatible(
            _identity(),
            _identity(track="agentic", model="gpt-6-astra", effort="xhigh", auth_surface="api_key"),
        )


def test_incompatible_suite_hash_rejected() -> None:
    left = {
        "track": "codex_product",
        "model": "gpt-5.6-sol",
        "effort": "max",
        "client_mode": "latest",
        "suite_id": "canary",
        "suite_version": "v1",
        "suite_hash": "aaa",
        "grading_protocol": "hidden_final_state",
        "auth_surface": "chatgpt",
    }
    right = dict(left)
    right["suite_hash"] = "bbb"
    with pytest.raises(ValueError, match="hash"):
        compare_identities(left, right)
