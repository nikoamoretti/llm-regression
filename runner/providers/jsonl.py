"""Parse Codex --json JSONL without crashing on unknown future events."""

from __future__ import annotations

import json
from typing import Any


KNOWN_EVENT_TYPES = {
    "thread.started",
    "turn.started",
    "turn.completed",
    "turn.failed",
    "item.started",
    "item.updated",
    "item.completed",
    "error",
}


def parse_jsonl(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    events: list[dict[str, Any]] = []
    malformed: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            malformed.append(stripped[:500])
            events.append({"type": "malformed_jsonl", "raw": stripped[:2000]})
            continue
        if isinstance(payload, dict):
            events.append(payload)
        else:
            events.append({"type": "non_object_jsonl", "value": payload})
    return events, malformed


def extract_thread_id(events: list[dict[str, Any]]) -> str | None:
    for event in events:
        if event.get("type") == "thread.started":
            return event.get("thread_id") or (event.get("thread") or {}).get("id")
        if event.get("thread_id"):
            return str(event["thread_id"])
    return None


def extract_usage(events: list[dict[str, Any]]) -> dict[str, Any]:
    usage: dict[str, Any] = {}
    for event in events:
        if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
            usage = event["usage"]
        elif isinstance(event.get("usage"), dict):
            usage = event["usage"]
    return usage


def extract_final_message(events: list[dict[str, Any]]) -> str:
    texts: list[str] = []
    for event in events:
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        if item.get("type") in {"agent_message", "message"} and item.get("text"):
            texts.append(str(item["text"]))
    return texts[-1] if texts else ""


def extract_commands(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    commands = []
    for event in events:
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "command_execution":
            commands.append(
                {
                    "command": item.get("command"),
                    "status": item.get("status"),
                    "exit_code": item.get("exit_code"),
                    "event_type": event.get("type"),
                }
            )
        elif event.get("type") in {"item.command_execution"}:
            commands.append(event)
    return commands


def extract_file_changes(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    changes = []
    for event in events:
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "file_change":
            changes.append(item)
        elif event.get("type") in {"item.file_change"}:
            changes.append(event)
    return changes


def extract_tool_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tools = []
    for event in events:
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") in {
            "command_execution",
            "file_change",
            "mcp_tool_call",
            "tool",
            "function_call",
        }:
            tools.append({"event_type": event.get("type"), "item": item})
        elif str(event.get("type") or "").startswith("item."):
            tools.append(event)
    return tools


def extract_verified_effort(events: list[dict[str, Any]], metadata: dict[str, Any] | None = None) -> str | None:
    if metadata:
        for key in ("verified_effort", "effective_effort", "model_reasoning_effort"):
            if metadata.get(key):
                return str(metadata[key])
    for event in events:
        for key in ("verified_effort", "effective_effort", "model_reasoning_effort"):
            if event.get(key):
                return str(event[key])
        config = event.get("config") or event.get("metadata") or {}
        if isinstance(config, dict):
            for key in ("model_reasoning_effort", "verified_effort", "effort"):
                if config.get(key):
                    return str(config[key])
        reasoning = event.get("reasoning")
        if isinstance(reasoning, dict) and reasoning.get("effort"):
            return str(reasoning["effort"])
    return None


def extract_verified_model(events: list[dict[str, Any]], metadata: dict[str, Any] | None = None) -> str | None:
    if metadata and metadata.get("verified_model"):
        return str(metadata["verified_model"])
    if metadata and metadata.get("model"):
        return str(metadata["model"])
    for event in events:
        if event.get("verified_model"):
            return str(event["verified_model"])
        if event.get("model"):
            return str(event["model"])
        config = event.get("config") or {}
        if isinstance(config, dict) and config.get("model"):
            return str(config["model"])
    return None


def classify_provider_error(
    events: list[dict[str, Any]], stderr: str, exit_code: int
) -> tuple[bool, str | None]:
    blob = (stderr or "").lower() + "\n" + json_blob(events).lower()
    if "429" in blob or "rate limit" in blob:
        return True, "http_429"
    if any(code in blob for code in (" 500", " 502", " 503", " 504")) or "internal server" in blob:
        return True, "http_5xx"
    if "connection" in blob and ("reset" in blob or "refused" in blob or "timeout" in blob):
        return True, "network"
    for event in events:
        if event.get("type") in {"error", "turn.failed"}:
            message = str(event.get("message") or event.get("error") or "")
            lower = message.lower()
            if "429" in lower or "rate limit" in lower:
                return True, "http_429"
            if any(code in lower for code in ("500", "502", "503", "504")):
                return True, "http_5xx"
    if exit_code not in {0, None} and not events:
        return True, "empty_crash"
    if exit_code is not None and int(exit_code) < 0:
        return True, "process_crash"
    return False, None


def json_blob(events: list[dict[str, Any]]) -> str:
    return json.dumps(events, default=str)


def summarize_jsonl(events: list[dict[str, Any]]) -> dict[str, Any]:
    usage = extract_usage(events)
    details_in = usage.get("input_tokens_details") or {}
    details_out = usage.get("output_tokens_details") or {}
    return {
        "thread_id": extract_thread_id(events),
        "final_text": extract_final_message(events),
        "usage": usage,
        "input_tokens": usage.get("input_tokens"),
        "cached_input_tokens": details_in.get("cached_tokens", usage.get("cached_input_tokens")),
        "output_tokens": usage.get("output_tokens"),
        "reasoning_output_tokens": details_out.get("reasoning_tokens", usage.get("reasoning_output_tokens")),
        "commands": extract_commands(events),
        "file_changes": extract_file_changes(events),
        "tool_events": extract_tool_events(events),
        "verified_effort": extract_verified_effort(events),
        "verified_model": extract_verified_model(events),
        "unknown_event_types": sorted(
            {
                str(event.get("type"))
                for event in events
                if event.get("type") not in KNOWN_EVENT_TYPES
                and event.get("type") not in {"malformed_jsonl", "non_object_jsonl"}
            }
        ),
    }
