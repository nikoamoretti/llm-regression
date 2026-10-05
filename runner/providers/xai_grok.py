"""xAI Grok 4.6 Responses adapter."""

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

DEFAULT_ENDPOINT = "https://api.x.ai/v1/responses"


def grok_api_key() -> str:
    return os.environ.get("XAI_API_KEY", "")


class GrokProvider:
    name = "xai"
    track = "model_only"

    def __init__(
        self,
        *,
        model: str = "grok-4.6",
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
        key = api_key if api_key is not None else grok_api_key()
        if not key:
            raise InvalidConfigurationError("Grok requires XAI_API_KEY")
        self.api_key = key
        self.endpoint = (
            endpoint
            or os.environ.get("XAI_API_BASE")
            or spec.default_api_base
            or "https://api.x.ai/v1"
        ).rstrip("/")
        if not self.endpoint.endswith("/responses"):
            self.endpoint = self.endpoint + spec.responses_path
        self.client = client or ResponsesClient(
            provider="xai",
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
            send_sampling_params=self.spec.send_sampling_params,
            temperature=self.spec.temperature,
            top_p=self.spec.top_p,
            instructions=instructions,
            extra=extra,
        )
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
        return _to_provider_result(response, workspace, auth_surface="api_key")


def _to_provider_result(response, workspace: Path, auth_surface: str) -> ProviderResult:
    return ProviderResult(
        requested_model=response.requested_model,
        verified_model=response.returned_model,
        requested_effort=response.requested_effort,
        verified_effort=response.verified_effort,
        auth_surface=auth_surface,
        thread_id=response.response_id,
        final_text=response.output_text,
        usage=response.usage,
        events=[{"type": "responses.completed", "response": response.raw_response}],
        raw_jsonl=response.raw_text,
        stdout=response.raw_text,
        exit_code=0 if not response.error_code else 1,
        retry_count=response.retry_count,
        latency_ms=response.latency_ms,
        command=[response.provider, response.requested_model, response.requested_effort],
        invalid_configuration=response.invalid_configuration,
        infrastructure=response.infrastructure,
        error_code=response.error_code,
        error=response.error,
        metadata={
            "request_hash": response.request_hash,
            "workspace": str(workspace),
            "http_status": response.http_status,
            "raw_headers": response.raw_headers,
        },
    )
