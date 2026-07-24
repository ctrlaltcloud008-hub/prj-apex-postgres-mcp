# pg-mcp — Enterprise Read-Only Postgres MCP Server

An MCP server that executes **agent-generated SQL** against named Postgres
**Environments**, strictly read-only, with layered enforcement, elicitation, audit
logging, and stdio + Streamable HTTP transports.

See [`docs/SPEC.md`](docs/SPEC.md), [`CONTEXT.md`](CONTEXT.md), and
[`docs/adr/`](docs/adr/) for the design of record.

## Quick start

```sh
uv sync
just validate-config config.example.yaml
just run-stdio -- --config config.example.yaml
```

## Read-only role prerequisite

Each Environment's `user` must be a read-only role. Provision it once per database:

```sql
CREATE ROLE mcp_readonly LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE mydb TO mcp_readonly;
GRANT USAGE ON SCHEMA public TO mcp_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcp_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO mcp_readonly;
```

The read-only role plus a read-only transaction are the authoritative write barrier
(see ADR 0002); the SQL gate is an advisory first layer.
