"""Server integration through the in-memory MCP client (primary seam, tickets 01/04/07).

Drives the real MCP server the way an agent would. Uses the SDK's in-memory client/server
session so no transport or network is involved.
"""

import json

from mcp.shared.memory import create_connected_server_and_client_session

from pg_mcp.app import App
from pg_mcp.config import AuditConfig, Config, EnvConfig, SecretsConfig


def _app_for(pg, tmp_path):
    import os

    os.environ["TEST_PG_PW"] = pg["password"]
    cfg = Config(
        envs={
            "test": EnvConfig(
                host=pg["host"],
                port=pg["port"],
                database=pg["database"],
                user=pg["user"],
                secrets=SecretsConfig(provider="env", password_key="TEST_PG_PW"),
            )
        },
        audit=AuditConfig(log_path=str(tmp_path / "audit.log")),
    )
    return App(cfg)


def _payload(result):
    """Extract the tool's JSON payload from a CallToolResult."""
    if getattr(result, "structuredContent", None):
        sc = result.structuredContent
        return sc.get("result", sc)
    return json.loads(result.content[0].text)


async def _call(app, tool, args):
    from pg_mcp.server import build_server

    mcp = build_server(app)
    async with create_connected_server_and_client_session(mcp._mcp_server) as client:
        return _payload(await client.call_tool(tool, args))


async def test_list_environments_no_db(tmp_path):
    cfg = Config(
        envs={
            "local": EnvConfig(
                host="h",
                database="d",
                user="u",
                secrets=SecretsConfig(provider="env", password_key="NOPE_UNSET"),
            )
        },
        audit=AuditConfig(log_path=str(tmp_path / "audit.log")),
    )
    app = App(cfg)
    out = await _call(app, "list_environments", {})
    assert out["environments"][0]["name"] == "local"
    assert out["environments"][0]["degraded"] is True  # secret unresolved -> degraded


async def test_run_query_happy_path(pg, tmp_path):
    app = _app_for(pg, tmp_path)
    out = await _call(
        app, "run_query", {"env": "test", "sql": "SELECT id, name FROM users ORDER BY id"}
    )
    assert out["row_count"] == 2
    assert out["rows"][0] == [1, "alice"]


async def test_run_query_rejects_write(pg, tmp_path):
    app = _app_for(pg, tmp_path)
    out = await _call(app, "run_query", {"env": "test", "sql": "DELETE FROM users"})
    assert out["error"]["code"] == "VALIDATION_NOT_SELECT"


async def test_run_query_unknown_env(pg, tmp_path):
    app = _app_for(pg, tmp_path)
    out = await _call(app, "run_query", {"env": "nope", "sql": "SELECT 1"})
    assert out["error"]["code"] == "ENV_UNKNOWN"


async def test_describe_table_via_client(pg, tmp_path):
    app = _app_for(pg, tmp_path)
    out = await _call(app, "describe_table", {"env": "test", "schema": "public", "table": "users"})
    assert out["primary_key"] == ["id"]


async def test_audit_log_written(pg, tmp_path):
    app = _app_for(pg, tmp_path)
    await _call(app, "run_query", {"env": "test", "sql": "SELECT 1"})
    lines = (tmp_path / "audit.log").read_text().strip().splitlines()
    events = [json.loads(x) for x in lines]
    rq = [e for e in events if e["tool"] == "run_query"]
    assert rq and rq[-1]["sql"] == "SELECT 1"
    assert rq[-1]["validation"] == "passed"
    assert rq[-1]["correlation_id"]
