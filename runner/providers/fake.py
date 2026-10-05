"""Deterministic fake Responses provider for offline tests.

Never treat fake output as scientific baseline data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from runner.providers.base import ProviderResult
from runner.providers.responses_common import NormalizedResponse, extract_function_calls


class FakeResponsesProvider:
    name = "fake"
    track = "model_only"

    def __init__(
        self,
        *,
        model: str,
        effort: str,
        provider: str = "fake",
        scenario: str = "ok",
        patch_text: str = "",
        final_text: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        http_status: int = 200,
        returned_model: str | None = None,
        returned_effort: str | None = None,
    ) -> None:
        self.model = model
        self.effort = effort
        self.provider = provider
        self.scenario = scenario
        self.patch_text = patch_text
        self.final_text = final_text
        self.tool_calls = tool_calls or []
        self.http_status = http_status
        self.returned_model = returned_model or model
        self.returned_effort = returned_effort or effort
        self.calls = 0

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
    ) -> NormalizedResponse:
        self.calls += 1
        _ = (input_messages, timeout_seconds, tools, instructions, previous_response_id, extra)
        if self.scenario == "429":
            return NormalizedResponse(
                provider=self.provider,
                requested_model=model,
                returned_model=None,
                requested_effort=effort,
                verified_effort=None,
                response_id=None,
                status="failed",
                output_text="",
                output_items=[],
                usage={},
                raw_headers={},
                raw_response={"error": "rate limited"},
                http_status=429,
                infrastructure=True,
                error_code="http_429",
                error={"message": "Fake HTTP 429", "status": 429},
            )
        if self.scenario == "5xx":
            return NormalizedResponse(
                provider=self.provider,
                requested_model=model,
                returned_model=None,
                requested_effort=effort,
                verified_effort=None,
                response_id=None,
                status="failed",
                output_text="",
                output_items=[],
                usage={},
                raw_headers={},
                raw_response={"error": "server"},
                http_status=503,
                infrastructure=True,
                error_code="http_5xx",
                error={"message": "Fake HTTP 503", "status": 503},
            )
        if self.scenario == "mismatch":
            return NormalizedResponse(
                provider=self.provider,
                requested_model=model,
                returned_model="other-model",
                requested_effort=effort,
                verified_effort="low",
                response_id="fake_mismatch",
                status="completed",
                output_text="",
                output_items=[],
                usage={},
                raw_headers={},
                raw_response={"model": "other-model", "reasoning": {"effort": "low"}},
                invalid_configuration=True,
                error_code="model_mismatch",
            )
        if self.tool_calls and self.calls == 1:
            items = []
            for index, call in enumerate(self.tool_calls):
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call.get("call_id", f"call_{index}"),
                        "name": call["name"],
                        "arguments": call.get("arguments") or {},
                    }
                )
            payload = {"id": "fake_tools", "model": self.returned_model, "output": items}
            return NormalizedResponse(
                provider=self.provider,
                requested_model=model,
                returned_model=self.returned_model,
                requested_effort=effort,
                verified_effort=self.returned_effort,
                response_id="fake_tools",
                status="completed",
                output_text="",
                output_items=items,
                usage={"input_tokens": 1, "output_tokens": 1},
                raw_headers={},
                raw_response=payload,
            )
        text = self.final_text
        if text is None:
            if self.patch_text:
                text = '{"patch": ' + _json_string(self.patch_text) + ', "summary": "fake"}'
            else:
                text = '{"patch": "", "summary": "fake empty"}'
        payload = {
            "id": f"fake_{self.calls}",
            "model": self.returned_model,
            "reasoning": {"effort": self.returned_effort},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
            "usage": {"input_tokens": 3, "output_tokens": 2},
        }
        return NormalizedResponse(
            provider=self.provider,
            requested_model=model,
            returned_model=self.returned_model,
            requested_effort=effort,
            verified_effort=self.returned_effort,
            response_id=payload["id"],
            status="completed",
            output_text=text,
            output_items=payload["output"],
            usage={"input_tokens": 3, "output_tokens": 2},
            raw_headers={},
            raw_response=payload,
            raw_text=text,
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
    ) -> NormalizedResponse:
        _ = (previous_response_id, tool_results, tools)
        return self.create_response(
            input_messages="tool-results",
            model=model,
            effort=effort,
            timeout_seconds=timeout_seconds,
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
        return ProviderResult(
            requested_model=model,
            verified_model=response.returned_model,
            requested_effort=effort,
            verified_effort=response.verified_effort,
            auth_surface="fake",
            thread_id=response.response_id,
            final_text=response.output_text,
            usage=response.usage,
            events=[{"type": "responses.completed", "response": response.raw_response}],
            raw_jsonl=response.raw_text,
            stdout=response.raw_text,
            invalid_configuration=response.invalid_configuration,
            infrastructure=response.infrastructure,
            error_code=response.error_code,
            error=response.error,
            metadata={"workspace": str(workspace), "scientific_data": False},
        )


def _json_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def parse_fake_tool_calls(response: NormalizedResponse) -> list[Any]:
    return extract_function_calls(response.raw_response)
