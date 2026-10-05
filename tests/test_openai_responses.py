import httpx
import pytest

from runner.errors import InvalidConfigurationError
from runner.providers.openai_responses import (
    OpenAIResponsesProvider,
    build_request,
    extract_output_text,
    request_hash,
    usage_fields,
)


def test_extract_text_from_responses_payload() -> None:
    payload = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "hello"}, {"type": "output_text", "text": "world"}],
            }
        ]
    }
    assert extract_output_text(payload) == "hello\nworld"


def test_usage_fields_prefer_nested_details() -> None:
    fields = usage_fields(
        {
            "input_tokens": 10,
            "output_tokens": 4,
            "input_tokens_details": {"cached_tokens": 3},
            "output_tokens_details": {"reasoning_tokens": 2},
            "cost_in_usd_ticks": 9,
        }
    )
    assert fields["cached_input_tokens"] == 3
    assert fields["reasoning_tokens"] == 2
    assert fields["cost_usd_ticks"] == 9


def test_build_request_rejects_penalties() -> None:
    with pytest.raises(ValueError):
        build_request("hi", effort="max", extra={"presence_penalty": 0.2})


def test_request_hash_is_stable() -> None:
    payload = build_request("hi", effort="max", model="gpt-5.6-sol")
    assert len(request_hash(payload)) == 64
    assert request_hash(payload) == request_hash(payload)


def test_retry_on_429_then_success(tmp_path) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="slow down", headers={"retry-after": "0"})
        payload = {
            "id": "resp_1",
            "model": "gpt-5.6-sol",
            "reasoning": {"effort": "max"},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    provider = OpenAIResponsesProvider(
        model="gpt-5.6-sol",
        effort="max",
        api_key="test",
        max_infra_retries=1,
    )
    with httpx.Client(transport=transport) as http:
        # Patch the provider's client construction by calling the request path directly
        # through a one-off monkeypatch of httpx.Client.
        original = httpx.Client

        class Wrapped(httpx.Client):
            def __init__(self, *args, **kwargs):
                kwargs["transport"] = transport
                super().__init__(*args, **kwargs)

        httpx.Client = Wrapped  # type: ignore[misc]
        try:
            result = provider.run_attempt(
                prompt="hello",
                workspace=tmp_path,
                model="gpt-5.6-sol",
                effort="max",
                timeout_seconds=30,
            )
        finally:
            httpx.Client = original  # type: ignore[misc]
    assert result.final_text == "ok"
    assert result.retry_count == 1
    assert result.verified_model == "gpt-5.6-sol"
    assert result.verified_effort == "max"
    assert calls["n"] == 2
    assert result.track if hasattr(result, "track") else True
    assert result.auth_surface == "api_key"
    assert not result.infrastructure


def test_missing_key() -> None:
    with pytest.raises(InvalidConfigurationError):
        OpenAIResponsesProvider(model="gpt-5.6-sol", effort="max", api_key="")
