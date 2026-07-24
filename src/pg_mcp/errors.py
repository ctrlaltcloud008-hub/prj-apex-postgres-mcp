"""Structured error contract (SPEC.md §6).

Every failure surfaces as an ``McpError`` carrying a stable ``code`` plus an
agent-legible ``message`` written so the model can self-correct.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    VALIDATION_MULTI_STATEMENT = "VALIDATION_MULTI_STATEMENT"
    VALIDATION_NOT_SELECT = "VALIDATION_NOT_SELECT"
    VALIDATION_WRITE_NODE = "VALIDATION_WRITE_NODE"
    VALIDATION_LOCKING = "VALIDATION_LOCKING"
    VALIDATION_FORBIDDEN_FUNCTION = "VALIDATION_FORBIDDEN_FUNCTION"
    VALIDATION_UNPARSEABLE = "VALIDATION_UNPARSEABLE"
    ENV_UNKNOWN = "ENV_UNKNOWN"
    ENV_DEGRADED = "ENV_DEGRADED"
    AUTH_FAILED = "AUTH_FAILED"
    TIMEOUT = "TIMEOUT"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    CONFIRMATION_DENIED = "CONFIRMATION_DENIED"
    INTERNAL = "INTERNAL"


class McpError(Exception):
    """An error with a stable code, returned to the agent as a structured payload."""

    def __init__(self, code: ErrorCode, message: str, detail: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail

    def to_payload(self) -> dict:
        err: dict = {"code": self.code.value, "message": self.message}
        if self.detail:
            err["detail"] = self.detail
        return {"error": err}
