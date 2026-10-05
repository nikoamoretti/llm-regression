"""Typed failures that must not be conflated with model quality."""

from __future__ import annotations

from typing import Any


class InfrastructureError(Exception):
    """Transport / HTTP / provider availability failures."""

    def __init__(self, message: str, status: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


class HarnessError(Exception):
    """Runner, container, or grader-host failures independent of the model."""


class InvalidConfigurationError(Exception):
    """Requested model/effort/auth could not be verified."""

    def __init__(self, message: str, requested: dict[str, Any] | None = None, verified: dict[str, Any] | None = None):
        super().__init__(message)
        self.requested = requested or {}
        self.verified = verified or {}
