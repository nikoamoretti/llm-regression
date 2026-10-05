"""OpenAI GPT-6 Astra Responses adapter.

Astra must not receive temperature or top_p. store is always false.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from runner.errors import InvalidConfigurationError
from runner.models import resolve_model
from runner.providers.base import ProviderResult
from runner.providers.responses_common import (
    ResponsesClient,
    build_responses_body,
    function_call_outputs,
)
from runner.providers.xai_grok import _to_provider_result


def astra_api_key() -> str:
    return os.environ.get("ASTRA_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""


def astra_key_source() -> str:
    if os.environ.get("ASTRA_API_KEY"):
        return "ASTRA_API_KEY"
    if os.environ.get("OPENAI_API_KEY"):
        return "OPENAI_API_KEY"
    return ""


class AstraProvider:
    name = "openai"
    track = "model_only"

    def __init__(
        self,
        *,
        model: str = "gpt-6-astra",
        effort: str = "xhigh",
        api_key: str | None = None,
        endpoint: str | None = None,
        timeout_s: float = 1800.0,
        max_infra_retries: int = 2,
        client: ResponsesClient | None = None,
    ) -> None:
        spec = resolve_model(model)
        self.model = spec.api_model
        self.effort = effort
        self.spec = spec
        key = api_key if api_key is not None else astra_api_key()
        if not key:
            raise InvalidConfigurationError(
                "Astra requires OPENAI_API_KEY or the ASTRA_API_KEY alias"
            )
        self.api_key = key
        self.key_source = astra_key_source() or "argument"
        self.endpoint = (
            endpoint
            or os.environ.get("OPENAI_API_BASE")
            or os.environ.get("OPENAI_BASE_URL")
            or spec.default_api_base
            or "https://api.openai.com/v1"
        ).rstrip("/")
        if not self.endpoint.endswith("/responses"):
            self.endpoint = self.endpoint + spec.responses_path
        self.client = client or ResponsesClient(
            provider="openai",
            endpoint=self.endpoint,
            api_key=self.api_key,
            user_agent="grok-astra-regression/3.0",
            timeout_s=timeout_s,
            max_infra_retries=max_infra_retries,
        )

    def create_response(
        self,
        *,
        input_messages: list[dict[str, Any]] | str,
        model: str,
        effort: str,
        timeout_seconds: int,
        tools: list[dict[str, Any]] | None = None,
        instructions: str | None = None,
        previous_response_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ):
        body = build_responses_body(
            input_messages,
            model=model,
            effort=effort,
            store=False,
            tools=tools,
            previous_response_id=previous_response_id,
            send_sampling_params=False,
            instructions=instructions,
            extra=extra,
        )
        if "temperature" in body or "top_p" in body:
            raise InvalidConfigurationError("Astra request must not include temperature or top_p")
        return self.client.post(
            body,
            timeout_seconds=timeout_seconds,
            requested_model=model,
            requested_effort=effort,
        )

    def continue_with_tool_results(
        self,
        *,
        previous_response_id: str,
        tool_results: list[tuple[str, Any]],
        model: str,
        effort: str,
        timeout_seconds: int,
        tools: list[dict[str, Any]] | None = None,
    ):
        return self.create_response(
            input_messages=function_call_outputs(tool_results),
            model=model,
            effort=effort,
            timeout_seconds=timeout_seconds,
            tools=tools,
            previous_response_id=previous_response_id,
        )

    def run_attempt(
        self,
        *,
        prompt: str,
        workspace: Path,
        model: str,
        effort: str,
        timeout_seconds: int,
    ) -> ProviderResult:
        response = self.create_response(
            input_messages=prompt,
            model=model,
            effort=effort,
            timeout_seconds=timeout_seconds,
        )
        result = _to_provider_result(response, workspace, auth_surface="api_key")
        result.metadata["key_source"] = self.key_source
        return result
