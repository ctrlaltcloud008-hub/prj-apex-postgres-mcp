"""Schema introspection queries (SPEC.md §4, ticket 05).

All queries run through ``Environment.query_rows`` (read-only, parameterised). System
schemas are excluded by default.
"""

from __future__ import annotations

from .db import Environment

_SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")


def list_schemas(env: Environment) -> list[str]:
    rows = env.query_rows(
        """
        SELECT schema_name
        FROM information_schema.schemata
        WHERE schema_name <> ALL(%s) AND schema_name NOT LIKE 'pg_temp%%'
        ORDER BY schema_name
        """,
        (list(_SYSTEM_SCHEMAS),),
    )
    return [r[0] for r in rows]


def list_tables(env: Environment, schema: str) -> list[dict]:
    rows = env.query_rows(
        """
        SELECT c.relname,
               CASE c.relkind WHEN 'r' THEN 'table' WHEN 'v' THEN 'view'
                              WHEN 'm' THEN 'materialized view' ELSE c.relkind::text END,
               COALESCE(c.reltuples, 0)::bigint,
               obj_description(c.oid)
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relkind IN ('r', 'v', 'm')
        ORDER BY c.relname
        """,
        (schema,),
    )
    return [{"name": r[0], "kind": r[1], "row_estimate": r[2], "comment": r[3]} for r in rows]


def describe_table(env: Environment, schema: str, table: str) -> dict:
    columns = env.query_rows(
        """
        SELECT a.attname, format_type(a.atttypid, a.atttypmod),
               NOT a.attnotnull, pg_get_expr(d.adbin, d.adrelid),
               col_description(a.attrelid, a.attnum)
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE n.nspname = %s AND c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY a.attnum
        """,
        (schema, table),
    )
    pk = env.query_rows(
        """
        SELECT a.attname
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey)
        WHERE n.nspname = %s AND c.relname = %s AND i.indisprimary
        ORDER BY a.attnum
        """,
        (schema, table),
    )
    fks = env.query_rows(
        """
        SELECT con.conname, pg_get_constraintdef(con.oid),
               CASE WHEN con.conrelid = c.oid THEN 'outgoing' ELSE 'incoming' END
        FROM pg_constraint con
        JOIN pg_class c ON c.relname = %s
        JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = %s
        WHERE con.contype = 'f' AND (con.conrelid = c.oid OR con.confrelid = c.oid)
          AND c.relnamespace = n.oid
        ORDER BY 1
        """,
        (table, schema),
    )
    indexes = env.query_rows(
        """
        SELECT i.relname, pg_get_indexdef(i.oid)
        FROM pg_index x
        JOIN pg_class t ON t.oid = x.indrelid
        JOIN pg_class i ON i.oid = x.indexrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = %s AND t.relname = %s
        ORDER BY i.relname
        """,
        (schema, table),
    )
    comment = env.query_rows("SELECT obj_description(%s::regclass)", (f"{schema}.{table}",))
    return {
        "schema": schema,
        "table": table,
        "comment": comment[0][0] if comment else None,
        "columns": [
            {
                "name": c[0],
                "type": c[1],
                "nullable": c[2],
                "default": c[3],
                "comment": c[4],
            }
            for c in columns
        ],
        "primary_key": [r[0] for r in pk],
        "foreign_keys": [{"name": f[0], "definition": f[1], "direction": f[2]} for f in fks],
        "indexes": [{"name": i[0], "definition": i[1]} for i in indexes],
    }


def full_catalog(env: Environment) -> dict:
    """The schema resource payload: every non-system schema with its tables."""
    catalog = {}
    for schema in list_schemas(env):
        catalog[schema] = list_tables(env, schema)
    return {"environment": env.name, "schemas": catalog}
