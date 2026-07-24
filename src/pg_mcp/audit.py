"""Audit logging (SPEC.md §7, ticket 07).

One JSON-Lines record per tool call to a configurable file (never stdout — stdio owns
it). Secrets and result *data* are never logged; full SQL always is. A correlation id
links a run_query to its explain and its elicitation.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path


class AuditLogger:
    def __init__(self, log_path: str):
        self._path = Path(log_path)
        self._lock = threading.Lock()

    @staticmethod
    def new_correlation_id() -> str:
        return uuid.uuid4().hex

    def record(
        self,
        *,
        tool: str,
        env: str | None,
        transport: str,
        session_id: str | None = None,
        correlation_id: str | None = None,
        sql: str | None = None,
        validation: str = "not_applicable",
        elicitation: str = "not_required",
        outcome: str = "ok",
        row_count: int | None = None,
        truncated: bool | None = None,
        duration_ms: int | None = None,
        error_code: str | None = None,
    ) -> None:
        event = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event_id": uuid.uuid4().hex,
            "correlation_id": correlation_id,
            "session_id": session_id,
            "transport": transport,
            "env": env,
            "tool": tool,
            "sql": sql,
            "validation": validation,
            "elicitation": elicitation,
            "outcome": outcome,
            "row_count": row_count,
            "truncated": truncated,
            "duration_ms": duration_ms,
            "error_code": error_code,
        }
        line = json.dumps(event, default=str)
        with self._lock:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def record_env_transition(self, env: str, transport: str, reason: str) -> None:
        self.record(
            tool="__env_transition__",
            env=env,
            transport=transport,
            outcome="degraded",
            error_code="ENV_DEGRADED",
            validation=reason,
        )
