"""Read-Only Gate — advisory application-layer SQL validation (SPEC.md §3, ADR 0002).

This is the FIRST of four layers and is advisory: it gives fast rejection and
agent-legible errors. It is NOT the security boundary — the read-only role + read-only
transaction (in db.py) are authoritative. Findings verified against sqlglot in research
ticket 02.

Rules:
  * ``sqlglot.parse`` (never ``parse_one``) — exactly one non-None statement.
  * Top-level allowlist: Select / Union / Subquery / With.
  * Mandatory deep walk rejecting any write / DDL / Copy / Set / Grant / Command / Into
    / Lock node (writing CTEs keep a Select root, so the walk is essential).
  * Function-name denylist (config-extendable, never shrinkable).
  * Parse failure or ``exp.Command`` fallback -> reject (fail closed).
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from .errors import ErrorCode, McpError

_DIALECT = "postgres"

_ALLOWED_TOP = (exp.Select, exp.Union, exp.Subquery, exp.With)

# Nodes that must never appear anywhere in a read-only statement.
_WRITE_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Copy,
    exp.Set,
    exp.Grant,
    exp.Command,  # unparsed / utility statements degrade here -> fail closed
    exp.Into,  # SELECT ... INTO
)

# Side-effecting functions sqlglot cannot recognise as writes (parse as Anonymous/Func).
_BUILTIN_DENIED_FUNCTIONS = frozenset(
    {
        "pg_sleep",
        "pg_read_file",
        "pg_read_binary_file",
        "pg_stat_file",
        "pg_ls_dir",
        "lo_import",
        "lo_export",
        "dblink",
        "dblink_exec",
        "pg_terminate_backend",
        "pg_cancel_backend",
        "pg_reload_conf",
        "nextval",
        "setval",
        "pg_logical_emit_message",
        "set_config",
        "pg_advisory_lock",
        "pg_advisory_xact_lock",
        "pg_advisory_unlock",
    }
)


class ReadOnlyGate:
    def __init__(self, extra_denied_functions: list[str] | None = None):
        extra = {f.lower() for f in (extra_denied_functions or [])}
        # Extends, never shrinks, the built-in denylist.
        self._denied_functions = _BUILTIN_DENIED_FUNCTIONS | extra

    def validate(self, sql: str) -> exp.Expression:
        """Return the parsed statement if it is a safe read, else raise McpError."""
        try:
            statements = [s for s in sqlglot.parse(sql, dialect=_DIALECT) if s is not None]
        except sqlglot.errors.ParseError as e:
            raise McpError(
                ErrorCode.VALIDATION_UNPARSEABLE,
                "SQL could not be parsed; only a single SELECT statement is allowed.",
                detail=str(e).splitlines()[0] if str(e) else None,
            ) from e

        if len(statements) != 1:
            raise McpError(
                ErrorCode.VALIDATION_MULTI_STATEMENT,
                "Multiple SQL statements are not allowed; submit a single SELECT.",
            )

        root = statements[0]
        if not isinstance(root, _ALLOWED_TOP):
            raise McpError(
                ErrorCode.VALIDATION_NOT_SELECT,
                f"Only read queries are allowed; got a {type(root).__name__} statement.",
            )

        # Deep walk — writing CTEs and modifying subqueries hide under a Select root.
        for node in root.walk():
            if isinstance(node, _WRITE_NODES):
                code = (
                    ErrorCode.VALIDATION_NOT_SELECT
                    if isinstance(node, exp.Command)
                    else ErrorCode.VALIDATION_WRITE_NODE
                )
                raise McpError(
                    code,
                    "Query contains a write, DDL, or unsupported statement "
                    f"({type(node).__name__}); only pure SELECT reads are allowed.",
                )
            if isinstance(node, exp.Lock):
                raise McpError(
                    ErrorCode.VALIDATION_LOCKING,
                    "Row-locking clauses (FOR UPDATE / FOR SHARE) are not allowed.",
                )

        self._check_functions(root)
        return root

    def _check_functions(self, root: exp.Expression) -> None:
        for func in root.find_all(exp.Func, exp.Anonymous):
            name = (func.name or "").lower()
            if name in self._denied_functions:
                raise McpError(
                    ErrorCode.VALIDATION_FORBIDDEN_FUNCTION,
                    f"Function {name!r} is not permitted (potential side effects).",
                )
