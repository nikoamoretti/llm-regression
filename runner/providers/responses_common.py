"""Shared Responses-style HTTP client for Grok and Astra.

Do not send temperature/top_p unless the model catalog explicitly allows it.
Astra must never receive sampling parameters. store is always false.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from runner.hash_tree import canonical_hash

TRANSIENT_HTTP = {408, 429, 500, 502, 503, 504}
FORBIDDEN_REASONING_PARAMS = ("presence_penalty", "frequency_penalty", "stop")


@dataclass
class NormalizedResponse:
    provider: str
    requested_model: str
    returned_model: str | None
    requested_effort: str
    verified_effort: str | None
    response_id: str | None
    status: str
    output_text: str
    output_items: list[dict[str, Any]]
    usage: dict[str, Any]
    raw_headers: dict[str, str]
    raw_response: dict[str, Any]
    http_status: int = 200
    infrastructure: bool = False
    invalid_configuration: bool = False
    error_code: str | None = None
    error: dict[str, Any] | None = None
    latency_ms: float | None = None
    retry_count: int = 0
    request_hash: str = ""
    raw_text: str = ""


@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]
    raw: dict[str, Any] = field(default_factory=dict)


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


def extract_function_calls(payload: dict[str, Any]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for item in payload.get("output") or []:
        if item.get("type") not in {"function_call", "tool_call"}:
            continue
        raw_args = item.get("arguments") or item.get("input") or {}
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError:
                parsed = {"_unparsed": raw_args}
        elif isinstance(raw_args, dict):
            parsed = raw_args
        else:
            parsed = {}
        calls.append(
            ToolCall(
                call_id=str(item.get("call_id") or item.get("id") or ""),
                name=str(item.get("name") or item.get("function", {}).get("name") or ""),
                arguments=parsed if isinstance(parsed, dict) else {},
                raw=item,
            )
        )
    return calls


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


def build_responses_body(
    input_messages: list[dict[str, Any]] | str,
    *,
    model: str,
    effort: str,
    max_output_tokens: int = 16_384,
    store: bool = False,
    tools: list[dict[str, Any]] | None = None,
    parallel_tool_calls: bool = False,
    previous_response_id: str | None = None,
    send_sampling_params: bool = False,
    temperature: float | None = None,
    top_p: float | None = None,
    instructions: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if extra:
        banned = [key for key in FORBIDDEN_REASONING_PARAMS if key in extra]
        if banned:
            raise ValueError(f"Reasoning models cannot use {banned}.")
    body: dict[str, Any] = {
        "model": model,
        "input": input_messages,
        "reasoning": {"effort": effort},
        "max_output_tokens": max_output_tokens,
        "store": False if store is False else store,
    }
    if instructions:
        body["instructions"] = instructions
    if tools:
        body["tools"] = tools
        body["parallel_tool_calls"] = parallel_tool_calls
    if previous_response_id:
        body["previous_response_id"] = previous_response_id
    if send_sampling_params:
        if temperature is not None:
            body["temperature"] = temperature
        if top_p is not None:
            body["top_p"] = top_p
    if extra:
        body.update(extra)
    return body


def request_hash(payload: dict[str, Any]) -> str:
    return canonical_hash(payload)


def normalize_responses_api(
    *,
    provider: str,
    requested_model: str,
    requested_effort: str,
    raw: dict[str, Any],
    headers: dict[str, str],
    http_status: int = 200,
    raw_text: str = "",
) -> NormalizedResponse:
    usage = raw.get("usage") or {}
    returned_model = raw.get("model")
    verified_effort = ((raw.get("reasoning") or {}).get("effort")) or None
    invalid = False
    if returned_model and returned_model != requested_model:
        invalid = True
    if verified_effort and verified_effort != requested_effort:
        invalid = True
    return NormalizedResponse(
        provider=provider,
        requested_model=requested_model,
        returned_model=returned_model,
        requested_effort=requested_effort,
        verified_effort=verified_effort or requested_effort,
        response_id=raw.get("id"),
        status=str(raw.get("status") or "completed"),
        output_text=extract_output_text(raw),
        output_items=list(raw.get("output") or []),
        usage=usage_fields(usage),
        raw_headers=headers,
        raw_response=raw,
        http_status=http_status,
        invalid_configuration=invalid,
        error_code="model_mismatch" if invalid else None,
        raw_text=raw_text,
    )


def function_call_outputs(calls: list[tuple[str, Any]]) -> list[dict[str, Any]]:
    items = []
    for call_id, result in calls:
        items.append(
            {
                "type": "function_call_output",
                "call_id": call_id,
                "output": result if isinstance(result, str) else json.dumps(result, default=str),
            }
        )
    return items


def _backoff(retry: int, schedule: list[float] | None = None) -> float:
    if schedule and retry < len(schedule):
        return schedule[retry] + random.uniform(0.0, 0.25)
    return min(30.0, 2.0**retry) + random.uniform(0.0, 0.25)


class ResponsesClient:
    def __init__(
        self,
        *,
        provider: str,
        endpoint: str,
        api_key: str,
        user_agent: str,
        timeout_s: float = 1800.0,
        max_infra_retries: int = 2,
        backoff_seconds: list[float] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.provider = provider
        self.endpoint = endpoint
        self.api_key = api_key
        self.user_agent = user_agent
        self.timeout_s = timeout_s
        self.max_infra_retries = max_infra_retries
        self.backoff_seconds = backoff_seconds or [5.0, 30.0]
        self.transport = transport

    def post(
        self,
        body: dict[str, Any],
        *,
        timeout_seconds: float,
        requested_model: str,
        requested_effort: str,
    ) -> NormalizedResponse:
        hashed = request_hash(body)
        started = time.perf_counter()
        last_error: dict[str, Any] | None = None
        retry_count = 0
        client_kwargs: dict[str, Any] = {"timeout": min(timeout_seconds, self.timeout_s)}
        if self.transport is not None:
            client_kwargs["transport"] = self.transport
        with httpx.Client(**client_kwargs) as client:
            for retry in range(self.max_infra_retries + 1):
                retry_count = retry
                request_start = time.perf_counter()
                try:
                    response = client.post(
                        self.endpoint,
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                            "User-Agent": self.user_agent,
                        },
                        json=body,
                    )
                except (httpx.TimeoutException, httpx.NetworkError, httpx.TransportError) as exc:
                    last_error = {"message": f"{self.provider} connection failure: {exc}", "retry": retry}
                    if retry == self.max_infra_retries:
                        return NormalizedResponse(
                            provider=self.provider,
                            requested_model=requested_model,
                            returned_model=None,
                            requested_effort=requested_effort,
                            verified_effort=None,
                            response_id=None,
                            status="failed",
                            output_text="",
                            output_items=[],
                            usage={},
                            raw_headers={},
                            raw_response={},
                            infrastructure=True,
                            error_code="network",
                            error=last_error,
                            retry_count=retry_count,
                            latency_ms=(time.perf_counter() - started) * 1000,
                            request_hash=hashed,
                        )
                    time.sleep(_backoff(retry, self.backoff_seconds))
                    continue

                latency_ms = (time.perf_counter() - request_start) * 1000
                raw_text = response.text
                try:
                    payload = response.json()
                except ValueError:
                    payload = {"raw": raw_text}

                if response.is_success:
                    normalized = normalize_responses_api(
                        provider=self.provider,
                        requested_model=requested_model,
                        requested_effort=requested_effort,
                        raw=payload if isinstance(payload, dict) else {"raw": payload},
                        headers=dict(response.headers),
                        http_status=response.status_code,
                        raw_text=raw_text,
                    )
                    normalized.latency_ms = latency_ms
                    normalized.retry_count = retry_count
                    normalized.request_hash = hashed
                    return normalized

                transient = response.status_code in TRANSIENT_HTTP
                code = "http_429" if response.status_code == 429 else (
                    "http_5xx" if response.status_code >= 500 else f"http_{response.status_code}"
                )
                last_error = {
                    "message": f"{self.provider} HTTP {response.status_code}: {raw_text[:2000]}",
                    "status": response.status_code,
                }
                if not transient or retry == self.max_infra_retries:
                    return NormalizedResponse(
                        provider=self.provider,
                        requested_model=requested_model,
                        returned_model=None,
                        requested_effort=requested_effort,
                        verified_effort=None,
                        response_id=None,
                        status="failed",
                        output_text="",
                        output_items=[],
                        usage={},
                        raw_headers=dict(response.headers),
                        raw_response=payload if isinstance(payload, dict) else {"raw": raw_text},
                        http_status=response.status_code,
                        infrastructure=transient,
                        error_code=code if transient else f"http_{response.status_code}",
                        error=last_error,
                        latency_ms=latency_ms,
                        retry_count=retry_count,
                        request_hash=hashed,
                        raw_text=raw_text,
                    )
                retry_after = response.headers.get("retry-after")
                if retry_after and str(retry_after).replace(".", "", 1).isdigit():
                    time.sleep(float(retry_after))
                else:
                    time.sleep(_backoff(retry, self.backoff_seconds))

        return NormalizedResponse(
            provider=self.provider,
            requested_model=requested_model,
            returned_model=None,
            requested_effort=requested_effort,
            verified_effort=None,
            response_id=None,
            status="failed",
            output_text="",
            output_items=[],
            usage={},
            raw_headers={},
            raw_response={},
            infrastructure=True,
            error_code="network",
            error=last_error or {"message": "unreachable retry loop"},
            request_hash=hashed,
        )
