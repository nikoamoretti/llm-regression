"""Frozen protocol versions for the dual-model experiment."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
PROTOCOLS_PATH = ROOT / "configs" / "protocols.yaml"

TOOL_PROTOCOL_VERSION = "2.0.0"
AGENTIC_TOOLS = (
    "list_files",
    "read_file",
    "search_text",
    "write_file",
    "apply_patch",
    "run_command",
)


@lru_cache(maxsize=1)
def load_protocols(path: Path | None = None) -> dict[str, Any]:
    target = path or PROTOCOLS_PATH
    return yaml.safe_load(target.read_text()) or {}


def tool_protocol_version() -> str:
    return str(load_protocols().get("tool_protocol_version") or TOOL_PROTOCOL_VERSION)


def pairing_protocol_version() -> str:
    return str(load_protocols().get("pairing_protocol_version") or "1.0.0")


def prompt_protocol_version() -> str:
    return str(load_protocols().get("prompt_protocol_version") or "1.0.0")
