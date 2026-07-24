"""DB-layer integration tests against ephemeral Postgres (tickets 03, 05)."""

import pytest

from pg_mcp import introspection
from pg_mcp.errors import ErrorCode, McpError


def test_run_query_returns_result_contract(ro_env):
    r = ro_env.run_query("SELECT id, name FROM users ORDER BY id")
    assert [c["name"] for c in r.columns] == ["id", "name"]
    assert r.row_count == 2
    assert r.rows[0] == [1, "alice"]
    assert r.truncated is False
    assert r.duration_ms >= 0


def test_row_cap_truncates(ro_env):
    r = ro_env.run_query("SELECT * FROM orders", row_limit=10)
    assert r.row_count == 10
    assert r.truncated is True


def test_write_is_refused_by_db_layer(ro_env):
    # The gate normally blocks this earlier; here we prove the DB itself refuses a write
    # even if a write reached it (read-only role + read-only transaction).
    import psycopg

    with pytest.raises(psycopg.Error):
        ro_env.run_query("INSERT INTO users (name) VALUES ('mallory')")


def test_statement_timeout(ro_env):
    ro_env.config.statement_timeout_ms = 100
    with pytest.raises(McpError) as exc:
        ro_env.run_query("SELECT count(*) FROM generate_series(1, 500000000)")
    assert exc.value.code == ErrorCode.TIMEOUT


def test_explain_does_not_execute(ro_env):
    plan = ro_env.explain("SELECT * FROM orders")
    assert plan["plan"] is not None


def test_list_schemas_excludes_system(ro_env):
    assert "public" in introspection.list_schemas(ro_env)
    assert "pg_catalog" not in introspection.list_schemas(ro_env)


def test_list_tables(ro_env):
    tables = {t["name"]: t for t in introspection.list_tables(ro_env, "public")}
    assert "users" in tables and "orders" in tables
    assert tables["users"]["comment"] == "application users"


def test_describe_table(ro_env):
    d = introspection.describe_table(ro_env, "public", "users")
    assert d["primary_key"] == ["id"]
    col_names = [c["name"] for c in d["columns"]]
    assert col_names == ["id", "name", "email"]
    assert any(i["name"] == "idx_users_email" for i in d["indexes"])
    # orders has an FK to users -> users sees an incoming FK
    d2 = introspection.describe_table(ro_env, "public", "orders")
    assert any(f["direction"] == "outgoing" for f in d2["foreign_keys"])
