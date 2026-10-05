"""Secondary diagnostic control: direct OpenAI Responses API.

This is not the system of record. It exists so a Codex-product change can be
compared against raw API behavior. The two tracks must never be mixed.
"""

from __future__ import annotations

import os
import random
import time
from pathlib import Path
from typing import Any

import httpx

from runner.errors import InvalidConfigurationError
from runner.hash_tree import canonical_hash
from runner.providers.base import ProviderResult

DEFAULT_BASE = "https://api.openai.com/v1"
FORBIDDEN_REASONING_PARAMS = ("presence_penalty", "frequency_penalty", "stop")


def extract_output_text(payload: dict[str, Any]) -> str:
    if payload.get("output_text"):
        return str(payload["output_text"])
    chunks: list[str] = []
    for item in payload.get("output") or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                chunks.append(str(content["text"]))
    return "\n".join(chunks)


def extract_function_calls(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in (payload.get("output") or []) if item.get("type") == "function_call"]


def usage_fields(usage: dict[str, Any] | None) -> dict[str, Any]:
    usage = usage or {}
    details_in = usage.get("input_tokens_details") or {}
    details_out = usage.get("output_tokens_details") or {}
    return {
        "input_tokens": usage.get("input_tokens"),
        "cached_input_tokens": details_in.get("cached_tokens", usage.get("cached_input_tokens")),
        "output_tokens": usage.get("output_tokens"),
        "reasoning_tokens": details_out.get("reasoning_tokens", usage.get("reasoning_tokens")),
        "total_tokens": usage.get("total_tokens"),
        "cost_usd_ticks": usage.get("cost_in_usd_ticks"),
    }


def build_request(
    input_messages: list[dict[str, Any]] | str,
    *,
    effort: str,
    max_output_tokens: int = 16_384,
    temperature: float = 0,
    top_p: float = 1,
    store: bool = False,
    tools: list[dict[str, Any]] | None = None,
    parallel_tool_calls: bool | None = None,
    previous_response_id: str | None = None,
    model: str = "gpt-5.6-sol",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if extra:
        banned = [key for key in FORBIDDEN_REASONING_PARAMS if key in extra]
        if banned:
            raise ValueError(f"Reasoning models cannot use {banned}.")
    request: dict[str, Any] = {
        "model": model,
        "input": input_messages,
        "reasoning": {"effort": effort},
        "temperature": temperature,
        "top_p": top_p,
        "max_output_tokens": max_output_tokens,
        "store": store,
    }
    if tools is not None:
        request["tools"] = tools
    if parallel_tool_calls is not None:
        request["parallel_tool_calls"] = parallel_tool_calls
    if previous_response_id:
        request["previous_response_id"] = previous_response_id
    if extra:
        request.update(extra)
    return request


def request_hash(payload: dict[str, Any]) -> str:
    return canonical_hash(payload)


def _backoff(retry: int) -> float:
    return min(30.0, 2.0**retry) + random.uniform(0.0, 0.25)


class OpenAIResponsesProvider:
    name = "openai_responses"
    track = "responses_control"

    def __init__(
        self,
        *,
        model: str,
        effort: str,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_s: float = 1200.0,
        max_infra_retries: int = 0,
    ) -> None:
        self.model = model
        self.effort = effort
        self.api_key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL") or DEFAULT_BASE).rstrip("/")
        self.timeout_s = timeout_s
        self.max_infra_retries = max_infra_retries
        if not self.api_key:
            raise InvalidConfigurationError(
                "responses_control requires OPENAI_API_KEY; the Codex product track does not."
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
        request = build_request(prompt, effort=effort, model=model)
        hashed = request_hash(request)
        started = time.perf_counter()
        last_error: dict[str, Any] | None = None
        retry_count = 0
        with httpx.Client(timeout=min(timeout_seconds, self.timeout_s)) as client:
            for retry in range(self.max_infra_retries + 1):
                retry_count = retry
                request_start = time.perf_counter()
                try:
                    response = client.post(
                        f"{self.base_url}/responses",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                            "User-Agent": "sol-regression/2.0",
                        },
                        json=request,
                    )
                except (httpx.TimeoutException, httpx.NetworkError, httpx.TransportError) as exc:
                    last_error = {"message": f"OpenAI connection failure: {exc}", "retry": retry}
                    if retry == self.max_infra_retries:
                        return ProviderResult(
                            requested_model=model,
                            verified_model=None,
                            requested_effort=effort,
                            verified_effort=None,
                            auth_surface="api_key",
                            command=["openai.responses", model, effort],
                            infrastructure=True,
                            error_code="network",
                            error=last_error,
                            retry_count=retry_count,
                            wall_ms=(time.perf_counter() - started) * 1000,
                            metadata={"request_hash": hashed, "workspace": str(workspace)},
                        )
                    time.sleep(_backoff(retry))
                    continue

                latency_ms = (time.perf_counter() - request_start) * 1000
                raw_text = response.text
                try:
                    payload = response.json()
                except ValueError:
                    payload = {"raw": raw_text}

                if response.is_success:
                    usage = payload.get("usage") or {}
                    verified_model = payload.get("model") or model
                    verified_effort = ((payload.get("reasoning") or {}).get("effort")) or effort
                    invalid = verified_model != model or verified_effort != effort
                    event = {"type": "responses.completed", "response": payload}
                    return ProviderResult(
                        requested_model=model,
                        verified_model=verified_model,
                        requested_effort=effort,
                        verified_effort=verified_effort,
                        auth_surface="api_key",
                        thread_id=payload.get("id"),
                        final_text=extract_output_text(payload),
                        usage=usage,
                        events=[event],
                        raw_jsonl=raw_text,
                        stdout=raw_text,
                        exit_code=0,
                        retry_count=retry_count,
                        latency_ms=latency_ms,
                        wall_ms=(time.perf_counter() - started) * 1000,
                        command=["openai.responses", model, effort],
                        invalid_configuration=invalid,
                        error_code="model_mismatch" if invalid else None,
                        metadata={
                            "request_hash": hashed,
                            "usage_fields": usage_fields(usage),
                            "workspace": str(workspace),
                        },
                    )

                transient = response.status_code in {429, 500, 502, 503, 504}
                code = "http_429" if response.status_code == 429 else "http_5xx"
                last_error = {
                    "message": f"OpenAI HTTP {response.status_code}: {raw_text[:2000]}",
                    "status": response.status_code,
                }
                if not transient or retry == self.max_infra_retries:
                    return ProviderResult(
                        requested_model=model,
                        verified_model=None,
                        requested_effort=effort,
                        verified_effort=None,
                        auth_surface="api_key",
                        raw_jsonl=raw_text,
                        stdout=raw_text,
                        exit_code=response.status_code,
                        retry_count=retry_count,
                        latency_ms=latency_ms,
                        wall_ms=(time.perf_counter() - started) * 1000,
                        command=["openai.responses", model, effort],
                        infrastructure=transient,
                        error_code=code if transient else f"http_{response.status_code}",
                        error=last_error,
                        metadata={"request_hash": hashed},
                    )
                retry_after = response.headers.get("retry-after")
                if retry_after and str(retry_after).replace(".", "", 1).isdigit():
                    time.sleep(float(retry_after))
                else:
                    time.sleep(_backoff(retry))

        return ProviderResult(
            requested_model=model,
            verified_model=None,
            requested_effort=effort,
            verified_effort=None,
            auth_surface="api_key",
            infrastructure=True,
            error_code="network",
            error=last_error or {"message": "unreachable retry loop"},
            metadata={"request_hash": hashed},
        )

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "track": self.track,
            "model": self.model,
            "effort": self.effort,
            "base_url": self.base_url,
            "auth_surface": "api_key",
        }
