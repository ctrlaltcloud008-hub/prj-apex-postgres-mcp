"""Connection + read-only execution layer (SPEC.md §3, ADR 0002 layers 2-4).

An ``Environment`` owns a lazily-created bounded connection pool, connects as the
configured read-only role, and runs every statement inside ``BEGIN TRANSACTION READ
ONLY`` with ``SET LOCAL statement_timeout`` (always rolled back). This is the
authoritative write barrier — it stops writes even inside VOLATILE functions the gate
cannot see. Row caps truncate rather than error.

psycopg is synchronous; callers wrap these methods in ``asyncio.to_thread``.
"""

from __future__ import annotations

import threading
import time

import psycopg
from psycopg.rows import tuple_row
from psycopg_pool import ConnectionPool

from .config import EnvConfig
from .errors import ErrorCode, McpError
from .secrets import SecretResolutionError, resolve_secret


class QueryResult:
    def __init__(self, columns, rows, row_count, truncated, duration_ms):
        self.columns = columns
        self.rows = rows
        self.row_count = row_count
        self.truncated = truncated
        self.duration_ms = duration_ms

    def to_payload(self) -> dict:
        return {
            "columns": self.columns,
            "rows": self.rows,
            "row_count": self.row_count,
            "truncated": self.truncated,
            "duration_ms": self.duration_ms,
        }


class Environment:
    """A named connection target with a lazy pool and degraded-state tracking."""

    def __init__(self, name: str, config: EnvConfig, *, degraded_reason: str | None = None):
        self.name = name
        self.config = config
        self._degraded_reason = degraded_reason
        self._pool: ConnectionPool | None = None
        self._lock = threading.Lock()

    # --- state ---------------------------------------------------------------

    @property
    def protected(self) -> bool:
        return self.config.protected

    @property
    def degraded(self) -> bool:
        return self._degraded_reason is not None

    @property
    def degraded_reason(self) -> str | None:
        return self._degraded_reason

    def summary(self) -> dict:
        return {
            "name": self.name,
            "database": self.config.database,
            "protected": self.protected,
            "degraded": self.degraded,
            "degraded_reason": self._degraded_reason,
        }

    # --- connection ----------------------------------------------------------

    def _get_pool(self) -> ConnectionPool:
        """Lazily build the pool, (re)resolving the secret. Heals a degraded env."""
        with self._lock:
            if self._pool is not None:
                return self._pool
            try:
                password = resolve_secret(self.config.secrets)
            except SecretResolutionError as e:
                self._degraded_reason = str(e)
                raise McpError(
                    ErrorCode.ENV_DEGRADED,
                    f"Environment {self.name!r} is unavailable: secret unresolved.",
                    detail=str(e),
                ) from e
            conninfo = psycopg.conninfo.make_conninfo(
                host=self.config.host,
                port=self.config.port,
                dbname=self.config.database,
                user=self.config.user,
                password=password,
            )
            pool = ConnectionPool(conninfo, min_size=0, max_size=5, open=True)
            self._pool = pool
            self._degraded_reason = None
            return pool

    def _reset_pool(self) -> None:
        with self._lock:
            if self._pool is not None:
                self._pool.close()
                self._pool = None

    def _run(self, sql: str, *, max_rows: int, apply_cap: bool):
        """Execute sql read-only. Returns (columns, rows, truncated). Raises McpError."""
        pool = self._get_pool()
        attempted_reresolve = False
        while True:
            try:
                with pool.connection() as conn:
                    conn.read_only = True
                    with conn.cursor(row_factory=tuple_row) as cur:
                        cur.execute(
                            f"SET LOCAL statement_timeout = {int(self.config.statement_timeout_ms)}"
                        )
                        cur.execute(sql)
                        columns = (
                            [{"name": d.name} for d in cur.description] if cur.description else []
                        )
                        if apply_cap:
                            rows = cur.fetchmany(max_rows + 1)
                            truncated = len(rows) > max_rows
                            rows = rows[:max_rows]
                        else:
                            rows = cur.fetchall()
                            truncated = False
                    conn.rollback()
                return columns, [list(r) for r in rows], truncated
            except psycopg.errors.QueryCanceled as e:
                raise McpError(
                    ErrorCode.TIMEOUT,
                    f"Query exceeded the {self.config.statement_timeout_ms}ms timeout.",
                ) from e
            except psycopg.OperationalError as e:
                # Secret may have rotated — re-resolve once before giving up.
                if not attempted_reresolve:
                    attempted_reresolve = True
                    self._reset_pool()
                    pool = self._get_pool()
                    continue
                raise McpError(
                    ErrorCode.AUTH_FAILED,
                    f"Could not connect to environment {self.name!r}.",
                    detail=str(e).splitlines()[0] if str(e) else None,
                ) from e

    # --- public operations ---------------------------------------------------

    def run_query(self, sql: str, row_limit: int | None = None) -> QueryResult:
        cap = self.config.effective_max_rows()
        if row_limit is not None:
            cap = min(cap, row_limit)  # caps below env max only, never above
        start = time.monotonic()
        columns, rows, truncated = self._run(sql, max_rows=cap, apply_cap=True)
        duration_ms = int((time.monotonic() - start) * 1000)
        return QueryResult(columns, rows, len(rows), truncated, duration_ms)

    def explain(self, sql: str) -> dict:
        columns, rows, _ = self._run(f"EXPLAIN (FORMAT JSON) {sql}", max_rows=1, apply_cap=False)
        # EXPLAIN FORMAT JSON returns a single row / single column of JSON.
        plan = rows[0][0] if rows and rows[0] else None
        return {"plan": plan}

    def query_rows(self, sql: str, params: tuple = ()) -> list[list]:
        """Internal helper for introspection queries (parameterised, read-only)."""
        pool = self._get_pool()
        with pool.connection() as conn:
            conn.read_only = True
            with conn.cursor(row_factory=tuple_row) as cur:
                cur.execute(
                    f"SET LOCAL statement_timeout = {int(self.config.statement_timeout_ms)}"
                )
                cur.execute(sql, params)
                rows = cur.fetchall()
            conn.rollback()
        return [list(r) for r in rows]

    def close(self) -> None:
        self._reset_pool()
