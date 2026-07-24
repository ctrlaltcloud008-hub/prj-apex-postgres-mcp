"""Shared fixtures. Integration fixtures spin up an ephemeral Postgres via testcontainers
and skip cleanly when Docker is unavailable (the read-only role/txn guarantees cannot be
faked, so these tests need a real database — SPEC.md Testing Decisions).
"""

import os

import psycopg
import pytest

try:
    from testcontainers.postgres import PostgresContainer

    _HAVE_TC = True
except Exception:  # pragma: no cover
    _HAVE_TC = False

RO_PASSWORD = "ro_pass_123"

SEED_SQL = (
    f"""
CREATE TABLE users (
    id serial PRIMARY KEY,
    name text NOT NULL,
    email text
);
COMMENT ON TABLE users IS 'application users';
CREATE INDEX idx_users_email ON users (email);

CREATE TABLE orders (
    id serial PRIMARY KEY,
    user_id int NOT NULL REFERENCES users(id),
    total numeric NOT NULL,
    status text DEFAULT 'new'
);

INSERT INTO users (name, email) VALUES ('alice', 'a@x.com'), ('bob', 'b@x.com');
INSERT INTO orders (user_id, total, status)
    SELECT 1, g, 'new' FROM generate_series(1, 50) g;

CREATE ROLE ro LOGIN PASSWORD '{RO_PASSWORD}';
GRANT CONNECT ON DATABASE test TO ro;
GRANT USAGE ON SCHEMA public TO ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ro;
"""
)


def _docker_available() -> bool:
    if not _HAVE_TC:
        return False
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def pg():
    if not _docker_available():
        pytest.skip("Docker/testcontainers unavailable — skipping integration tests")
    with PostgresContainer("postgres:16-alpine") as container:
        admin_url = container.get_connection_url().replace("postgresql+psycopg2", "postgresql")
        with psycopg.connect(admin_url, autocommit=True) as conn:
            conn.execute(SEED_SQL)
        yield {
            "host": container.get_container_host_ip(),
            "port": int(container.get_exposed_port(5432)),
            "database": "test",
            "user": "ro",
            "password": RO_PASSWORD,
        }


@pytest.fixture
def ro_env(pg):
    """A read-only Environment pointed at the ephemeral Postgres."""
    from pg_mcp.config import EnvConfig, SecretsConfig
    from pg_mcp.db import Environment

    os.environ["TEST_PG_PW"] = pg["password"]
    cfg = EnvConfig(
        host=pg["host"],
        port=pg["port"],
        database=pg["database"],
        user=pg["user"],
        secrets=SecretsConfig(provider="env", password_key="TEST_PG_PW"),
    )
    env = Environment("test", cfg)
    yield env
    env.close()
