"""Elicitation / confirmation on Protected Environments (ticket 06)."""

import json
import os

from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import ElicitResult

from pg_mcp.app import App
from pg_mcp.config import AuditConfig, Config, EnvConfig, SecretsConfig
from pg_mcp.server import build_server


def _payload(result):
    if getattr(result, "structuredContent", None):
        sc = result.structuredContent
        return sc.get("result", sc)
    return json.loads(result.content[0].text)


def _protected_app(pg, tmp_path, allow_unconfirmed=False):
    os.environ["TEST_PG_PW"] = pg["password"]
    cfg = Config(
        envs={
            "prod": EnvConfig(
                host=pg["host"],
                port=pg["port"],
                database=pg["database"],
                user=pg["user"],
                protected=True,
                allow_unconfirmed=allow_unconfirmed,
                secrets=SecretsConfig(provider="env", password_key="TEST_PG_PW"),
            )
        },
        audit=AuditConfig(log_path=str(tmp_path / "audit.log")),
    )
    return App(cfg)


async def _call(app, tool, args, elicit_callback=None):
    mcp = build_server(app)
    async with create_connected_server_and_client_session(
        mcp._mcp_server, elicitation_callback=elicit_callback
    ) as client:
        return _payload(await client.call_tool(tool, args))


async def test_protected_no_limit_fails_closed_without_elicit(pg, tmp_path):
    app = _protected_app(pg, tmp_path)
    # No elicitation callback -> client cannot elicit -> fail closed.
    out = await _call(app, "run_query", {"env": "prod", "sql": "SELECT * FROM orders"})
    assert out["error"]["code"] == "CONFIRMATION_REQUIRED"


async def test_protected_with_limit_runs_without_prompt(pg, tmp_path):
    app = _protected_app(pg, tmp_path)
    out = await _call(app, "run_query", {"env": "prod", "sql": "SELECT * FROM orders LIMIT 5"})
    assert out["row_count"] == 5


async def test_allow_unconfirmed_bypasses(pg, tmp_path):
    app = _protected_app(pg, tmp_path, allow_unconfirmed=True)
    out = await _call(app, "run_query", {"env": "prod", "sql": "SELECT * FROM orders"})
    assert out["row_count"] == 50


async def test_elicit_accepted_runs(pg, tmp_path):
    app = _protected_app(pg, tmp_path)

    async def accept(context, params):
        return ElicitResult(action="accept", content={"confirm": True})

    out = await _call(
        app, "run_query", {"env": "prod", "sql": "SELECT * FROM orders"}, elicit_callback=accept
    )
    assert out["row_count"] == 50


async def test_elicit_declined_denied(pg, tmp_path):
    app = _protected_app(pg, tmp_path)

    async def decline(context, params):
        return ElicitResult(action="decline")

    out = await _call(
        app, "run_query", {"env": "prod", "sql": "SELECT * FROM orders"}, elicit_callback=decline
    )
    assert out["error"]["code"] == "CONFIRMATION_DENIED"
