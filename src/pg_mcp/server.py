"""MCP server: tools, resources, prompts, and elicitation (SPEC.md §4-6).

Tools are the primary (reliably model-invoked) surface; the schema is mirrored as a
resource. Every tool call is audited. Risky queries against a Protected Environment
elicit human confirmation, failing closed when the client cannot elicit.

psycopg is synchronous, so DB work runs in a worker thread via ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import json

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field
from sqlglot import exp

from . import introspection
from .app import App
from .errors import ErrorCode, McpError

# Elicitation thresholds (SPEC.md §5).
ROW_ESTIMATE_THRESHOLD = 10_000
COST_THRESHOLD = 1_000_000.0


class Confirmation(BaseModel):
    confirm: bool = Field(description="Confirm running this query against a protected environment?")


def build_server(app: App) -> FastMCP:
    mcp = FastMCP("pg-mcp")

    def _require_env(name: str):
        env = app.get_env(name)
        if env is None:
            raise McpError(
                ErrorCode.ENV_UNKNOWN,
                f"Unknown environment {name!r}. Call list_environments first.",
            )
        return env

    def _session_id(ctx: Context) -> str | None:
        try:
            return getattr(ctx, "client_id", None) or getattr(ctx, "request_id", None)
        except Exception:
            return None

    # --- tools ---------------------------------------------------------------

    @mcp.tool()
    async def list_environments() -> dict:
        """List the configured Postgres environments and their protected/degraded state."""
        envs = [e.summary() for e in app.environments.values()]
        app.audit.record(tool="list_environments", env=None, transport=app.transport)
        return {"environments": envs}

    @mcp.tool()
    async def list_schemas(env: str) -> dict:
        """List non-system schemas in an environment."""
        try:
            e = _require_env(env)
            schemas = await asyncio.to_thread(introspection.list_schemas, e)
            app.audit.record(tool="list_schemas", env=env, transport=app.transport)
            return {"schemas": schemas}
        except McpError as err:
            app.audit.record(
                tool="list_schemas",
                env=env,
                transport=app.transport,
                outcome="error",
                error_code=err.code.value,
            )
            return err.to_payload()

    @mcp.tool()
    async def list_tables(env: str, schema: str) -> dict:
        """List tables and views in a schema, with row estimates and comments."""
        try:
            e = _require_env(env)
            tables = await asyncio.to_thread(introspection.list_tables, e, schema)
            app.audit.record(tool="list_tables", env=env, transport=app.transport)
            return {"tables": tables}
        except McpError as err:
            app.audit.record(
                tool="list_tables",
                env=env,
                transport=app.transport,
                outcome="error",
                error_code=err.code.value,
            )
            return err.to_payload()

    @mcp.tool()
    async def describe_table(env: str, schema: str, table: str) -> dict:
        """Describe a table: columns, primary key, foreign keys (both directions),
        indexes, and comments — in one call."""
        try:
            e = _require_env(env)
            desc = await asyncio.to_thread(introspection.describe_table, e, schema, table)
            app.audit.record(tool="describe_table", env=env, transport=app.transport)
            return desc
        except McpError as err:
            app.audit.record(
                tool="describe_table",
                env=env,
                transport=app.transport,
                outcome="error",
                error_code=err.code.value,
            )
            return err.to_payload()

    @mcp.tool()
    async def explain_query(env: str, sql: str) -> dict:
        """Return the query plan (EXPLAIN, no execution) for a read query."""
        corr = app.audit.new_correlation_id()
        try:
            e = _require_env(env)
            app.gate.validate(sql)
            plan = await asyncio.to_thread(e.explain, sql)
            app.audit.record(
                tool="explain_query",
                env=env,
                transport=app.transport,
                correlation_id=corr,
                sql=sql,
                validation="passed",
            )
            return plan
        except McpError as err:
            app.audit.record(
                tool="explain_query",
                env=env,
                transport=app.transport,
                correlation_id=corr,
                sql=sql,
                outcome="error",
                validation=err.code.value,
                error_code=err.code.value,
            )
            return err.to_payload()

    @mcp.tool()
    async def run_query(env: str, sql: str, ctx: Context, row_limit: int | None = None) -> dict:
        """Execute a read-only SELECT against an environment and return rows."""
        corr = app.audit.new_correlation_id()
        session_id = _session_id(ctx)
        elicitation_outcome = "not_required"
        try:
            e = _require_env(env)
            if e.degraded:
                raise McpError(
                    ErrorCode.ENV_DEGRADED,
                    f"Environment {env!r} is degraded and cannot be queried.",
                    detail=e.degraded_reason,
                )
            root = app.gate.validate(sql)

            if e.protected and not e.config.allow_unconfirmed:
                needs = await _needs_confirmation(e, root, sql, row_limit)
                if needs is not None:
                    elicitation_outcome = await _confirm(ctx, env, sql, needs, corr, app)

            result = await asyncio.to_thread(e.run_query, sql, row_limit)
            app.audit.record(
                tool="run_query",
                env=env,
                transport=app.transport,
                session_id=session_id,
                correlation_id=corr,
                sql=sql,
                validation="passed",
                elicitation=elicitation_outcome,
                row_count=result.row_count,
                truncated=result.truncated,
                duration_ms=result.duration_ms,
            )
            return result.to_payload()
        except McpError as err:
            app.audit.record(
                tool="run_query",
                env=env,
                transport=app.transport,
                session_id=session_id,
                correlation_id=corr,
                sql=sql,
                outcome="error",
                validation=err.code.value,
                elicitation=elicitation_outcome,
                error_code=err.code.value,
            )
            return err.to_payload()

    # --- resource ------------------------------------------------------------

    @mcp.resource("postgres://{env}/schema")
    async def schema_resource(env: str) -> str:
        e = _require_env(env)
        catalog = await asyncio.to_thread(introspection.full_catalog, e)
        app.audit.record(tool="schema_resource", env=env, transport=app.transport)
        return json.dumps(catalog, default=str, indent=2)

    # --- prompts -------------------------------------------------------------

    @mcp.prompt()
    def draft_query(env: str, question: str) -> str:
        """Draft a safe, read-only SQL query answering a question."""
        return (
            f"Using the '{env}' Postgres environment, draft a single read-only SELECT "
            f"that answers: {question}\n\n"
            "First call list_schemas / list_tables / describe_table to learn the schema. "
            "Use explain_query to check cost before run_query. The server only permits "
            "SELECT statements."
        )

    @mcp.prompt()
    def summarize_schema(env: str) -> str:
        """Summarize the schema of an environment."""
        return (
            f"Read the postgres://{env}/schema resource (or call list_schemas and "
            f"list_tables) and summarize the '{env}' database: its main entities and how "
            "they relate."
        )

    @mcp.prompt()
    def explain_plan(env: str, sql: str) -> str:
        """Interpret the query plan for a SQL statement."""
        return (
            f"Call explain_query(env='{env}', sql=...) for the following SQL and explain "
            f"the plan in plain language, flagging expensive operations:\n\n{sql}"
        )

    return mcp


async def _needs_confirmation(env, root: exp.Expression, sql: str, row_limit: int | None):
    """Return a reason string if the query needs confirmation, else None."""
    has_limit = root.args.get("limit") is not None or row_limit is not None
    if not has_limit:
        return "the query has no LIMIT"
    try:
        plan = await asyncio.to_thread(env.explain, sql)
        node = (plan.get("plan") or [{}])[0].get("Plan", {})
        rows = node.get("Plan Rows", 0)
        cost = node.get("Total Cost", 0.0)
        if rows and rows > ROW_ESTIMATE_THRESHOLD:
            return f"estimated ~{rows} rows"
        if cost and cost > COST_THRESHOLD:
            return f"estimated cost {cost}"
    except McpError:
        return None
    return None


async def _confirm(ctx: Context, env: str, sql: str, reason: str, corr: str, app: App) -> str:
    """Elicit confirmation; fail closed if the client cannot elicit."""
    message = (
        f"Run this query against PROTECTED environment '{env}'? Reason for review: "
        f"{reason}.\n\n{sql}"
    )
    try:
        result = await ctx.elicit(message=message, schema=Confirmation)
    except Exception as e:
        raise McpError(
            ErrorCode.CONFIRMATION_REQUIRED,
            f"Confirmation is required to run this query against protected '{env}', but "
            "this client cannot prompt. Add a LIMIT or reduce the query's cost, or set "
            "allow_unconfirmed for this environment.",
            detail=str(e).splitlines()[0] if str(e) else None,
        ) from e

    accepted = getattr(result, "action", None) == "accept" and getattr(result, "data", None)
    if accepted and result.data.confirm:
        return "accepted"
    raise McpError(
        ErrorCode.CONFIRMATION_DENIED,
        f"Query against protected environment '{env}' was not confirmed.",
    )
