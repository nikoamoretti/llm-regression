from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from runner.baselines import create_baseline, qualify_runs
from runner.evaluate import evaluate
from runner.models import four_level_efforts, validate_model_effort_track
from runner.pairing import blocked_model_order, pair_key
from runner.patch import extract_structured_patch
from runner.providers.fake import FakeResponsesProvider
from runner.providers.openai_astra import AstraProvider
from runner.providers.responses_common import build_responses_body
from runner.providers.xai_grok import GrokProvider
from runner.storage import Store
from runner.tracks.model_only import repository_snapshot


def test_pair_key_ignores_model_and_date() -> None:
    left = pair_key(
        suite_hash="abc",
        task_id="BUG-PY-01",
        task_version="v1",
        track="agentic",
        effort="xhigh",
        repeat_index=0,
        schedule_seed=7,
    )
    right = pair_key(
        suite_hash="abc",
        task_id="BUG-PY-01",
        task_version="v1",
        track="agentic",
        effort="xhigh",
        repeat_index=0,
        schedule_seed=7,
    )
    assert left == right
    assert "grok" not in left and "astra" not in left


def test_blocked_model_order_is_deterministic_and_varies() -> None:
    models = ["grok-4.6", "gpt-6-astra"]
    first = blocked_model_order(models, pair_key_value="pair-aaa", schedule_seed=11)
    second = blocked_model_order(models, pair_key_value="pair-aaa", schedule_seed=11)
    orders = {
        tuple(blocked_model_order(models, pair_key_value=f"pair-{i}", schedule_seed=11))
        for i in range(32)
    }
    assert first == second
    assert set(first) == set(models)
    assert len(orders) == 2


def test_grok_rejects_max() -> None:
    with pytest.raises(Exception, match="max"):
        validate_model_effort_track("grok-4.6", "max", "agentic")


def test_astra_max_is_auxiliary() -> None:
    spec = validate_model_effort_track("gpt-6-astra", "max", "agentic")
    assert spec.is_auxiliary_effort("max")
    assert "max" not in four_level_efforts()


def test_astra_request_omits_sampling_params() -> None:
    body = build_responses_body(
        "hello",
        model="gpt-6-astra",
        effort="xhigh",
        send_sampling_params=False,
        temperature=0,
        top_p=1,
    )
    assert "temperature" not in body
    assert "top_p" not in body
    assert body["store"] is False


def test_grok_request_may_include_sampling_params() -> None:
    body = build_responses_body(
        "hello",
        model="grok-4.6",
        effort="xhigh",
        send_sampling_params=True,
        temperature=0,
        top_p=1,
    )
    assert body["temperature"] == 0
    assert body["top_p"] == 1
    assert body["store"] is False


def test_structured_patch_from_json() -> None:
    diff = """--- a/src/a.py
+++ b/src/a.py
@@ -1 +1 @@
-old
+new
"""
    text = json.dumps({"patch": diff, "summary": "fix", "files_expected_to_change": ["src/a.py"]})
    assert extract_structured_patch(text).startswith("--- a/src/a.py")


def test_snapshot_is_lexicographic(tmp_path: Path) -> None:
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    snap = repository_snapshot(tmp_path)
    assert snap.index("FILE: a.txt") < snap.index("FILE: b.txt")


def test_fake_429_is_infra(tmp_path: Path) -> None:
    provider = FakeResponsesProvider(model="grok-4.6", effort="xhigh", scenario="429")
    result = provider.run_attempt(
        prompt="x",
        workspace=tmp_path,
        model="grok-4.6",
        effort="xhigh",
        timeout_seconds=5,
    )
    assert result.infrastructure
    assert result.error_code == "http_429"


def test_fake_5xx_is_infra(tmp_path: Path) -> None:
    provider = FakeResponsesProvider(model="gpt-6-astra", effort="high", scenario="5xx")
    result = provider.run_attempt(
        prompt="x",
        workspace=tmp_path,
        model="gpt-6-astra",
        effort="high",
        timeout_seconds=5,
    )
    assert result.infrastructure
    assert result.error_code == "http_5xx"


def test_gold_cannot_become_baseline(tmp_path: Path) -> None:
    root = Path.cwd()
    run_ids = evaluate(
        root,
        "canary",
        efforts=["xhigh"],
        source="gold",
        tracks=["model_only"],
        models=["grok-4.6"],
        repeats=1,
        artifact_dir=tmp_path / "arts",
        database_url=f"sqlite:///{tmp_path / 'reg.db'}",
    )
    store = Store(f"sqlite:///{tmp_path / 'reg.db'}")
    run_id = next(iter(run_ids.values()))
    report = qualify_runs(store, [run_id])
    assert report["ok"] is False
    with pytest.raises(ValueError, match="scientific"):
        create_baseline(
            store,
            name="bad-gold",
            run_ids=[run_id],
            track="model_only",
            model="grok-4.6",
            effort="xhigh",
            suite_id="canary",
            suite_version="v1",
            suite_hash="x",
        )


def test_fake_evaluate_is_not_scientific(tmp_path: Path) -> None:
    root = Path.cwd()
    run_ids = evaluate(
        root,
        "canary",
        efforts=["low"],
        source="fake",
        tracks=["model_only"],
        models=["grok-4.6", "gpt-6-astra"],
        repeats=1,
        blocked_randomization=True,
        artifact_dir=tmp_path / "arts",
        database_url=f"sqlite:///{tmp_path / 'fake.db'}",
        run_seed=99,
    )
    store = Store(f"sqlite:///{tmp_path / 'fake.db'}")
    keys = set()
    models_seen = set()
    with store.engine.begin() as conn:
        from sqlalchemy import text

        rows = conn.execute(text("SELECT pair_key, requested_model, scientific_data, source FROM attempts")).mappings().all()
    for row in rows:
        keys.add(row["pair_key"])
        models_seen.add(row["requested_model"])
        assert int(row["scientific_data"]) == 0
        assert row["source"] == "fake"
    assert models_seen == {"grok-4.6", "gpt-6-astra"}
    assert keys  # pair keys exist
    # Same pair key is shared by both models.
    with store.engine.begin() as conn:
        from sqlalchemy import text

        grouped = conn.execute(
            text("SELECT pair_key, COUNT(DISTINCT requested_model) AS n FROM attempts GROUP BY pair_key")
        ).mappings().all()
    assert all(int(row["n"]) == 2 for row in grouped)


def test_astra_provider_rejects_missing_key() -> None:
    from runner.errors import InvalidConfigurationError

    with pytest.raises(InvalidConfigurationError):
        AstraProvider(model="gpt-6-astra", effort="xhigh", api_key="")


def test_grok_retry_on_429(tmp_path: Path) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        body = json.loads(request.content)
        assert "temperature" in body
        if calls["n"] == 1:
            return httpx.Response(429, text="slow", headers={"retry-after": "0"})
        return httpx.Response(
            200,
            json={
                "id": "resp_g",
                "model": "grok-4.6",
                "reasoning": {"effort": "xhigh"},
                "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"patch":"","summary":"ok"}'}]}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    transport = httpx.MockTransport(handler)
    from runner.providers.responses_common import ResponsesClient

    client = ResponsesClient(
        provider="xai",
        endpoint="https://api.x.ai/v1/responses",
        api_key="test",
        user_agent="test",
        max_infra_retries=1,
        backoff_seconds=[0, 0],
        transport=transport,
    )
    provider = GrokProvider(model="grok-4.6", effort="xhigh", api_key="test", client=client)
    result = provider.run_attempt(
        prompt="hi",
        workspace=tmp_path,
        model="grok-4.6",
        effort="xhigh",
        timeout_seconds=10,
    )
    assert result.final_text
    assert calls["n"] == 2
    assert not result.infrastructure


def test_astra_http_body_has_no_temperature(tmp_path: Path) -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "resp_a",
                "model": "gpt-6-astra",
                "reasoning": {"effort": "high"},
                "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"patch":"","summary":"ok"}'}]}],
            },
        )

    transport = httpx.MockTransport(handler)
    from runner.providers.responses_common import ResponsesClient

    client = ResponsesClient(
        provider="openai",
        endpoint="https://api.openai.com/v1/responses",
        api_key="test",
        user_agent="test",
        max_infra_retries=0,
        transport=transport,
    )
    provider = AstraProvider(model="gpt-6-astra", effort="high", api_key="test", client=client)
    provider.run_attempt(
        prompt="hi",
        workspace=tmp_path,
        model="gpt-6-astra",
        effort="high",
        timeout_seconds=10,
    )
    assert "temperature" not in seen
    assert "top_p" not in seen
    assert seen.get("store") is False
