"""Read-Only Gate regression corpus (ticket 04, pure unit seam).

Guards two failure modes: writes that slip through, and legit Postgres SELECTs that get
false-rejected. Pin the sqlglot version alongside this file.
"""

import pytest

from pg_mcp.errors import ErrorCode, McpError
from pg_mcp.gate import ReadOnlyGate

gate = ReadOnlyGate()

# Legitimate reads an analytics agent emits — MUST pass.
ALLOWED = [
    "SELECT 1",
    "SELECT * FROM users WHERE id = 5",
    "SELECT count(*) FROM orders GROUP BY status HAVING count(*) > 3",
    "WITH recent AS (SELECT * FROM orders WHERE created_at > now() - interval '1 day') SELECT * FROM recent",
    "SELECT u.name, o.total FROM users u JOIN orders o ON o.user_id = u.id",
    "SELECT * FROM a, LATERAL (SELECT * FROM b WHERE b.a_id = a.id) x",
    "SELECT data->>'name', data #> '{a,b}' FROM docs WHERE data @> '{\"x\":1}'",
    "SELECT id, row_number() OVER (PARTITION BY status ORDER BY created_at) FROM orders",
    "SELECT * FROM t WHERE tags = ANY(ARRAY['a','b']) AND nums[1:2] = ARRAY[1,2]",
    "SELECT DISTINCT ON (user_id) user_id, created_at FROM orders ORDER BY user_id, created_at DESC",
    "SELECT * FROM generate_series(1, 10) AS g(n)",
    "(SELECT 1) UNION (SELECT 2)",
]

# Writes / DDL / side effects / abuse — MUST be rejected.
REJECTED = [
    ("INSERT INTO t VALUES (1)", None),
    ("UPDATE t SET a = 1", None),
    ("DELETE FROM t", None),
    ("DROP TABLE t", None),
    ("CREATE TABLE t (a int)", None),
    ("ALTER TABLE t ADD COLUMN b int", None),
    ("TRUNCATE t", None),
    ("GRANT SELECT ON t TO bob", None),
    ("MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET a = 1", None),
    ("SELECT 1; DROP TABLE t", ErrorCode.VALIDATION_MULTI_STATEMENT),
    ("SELECT 1; SELECT 2", ErrorCode.VALIDATION_MULTI_STATEMENT),
    (
        "WITH x AS (INSERT INTO t VALUES (1) RETURNING *) SELECT * FROM x",
        ErrorCode.VALIDATION_WRITE_NODE,
    ),
    ("WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x", ErrorCode.VALIDATION_WRITE_NODE),
    ("SELECT * INTO backup FROM t", ErrorCode.VALIDATION_WRITE_NODE),
    ("SELECT * FROM t FOR UPDATE", ErrorCode.VALIDATION_LOCKING),
    ("SELECT * FROM t FOR SHARE", ErrorCode.VALIDATION_LOCKING),
    ("SELECT pg_sleep(10)", ErrorCode.VALIDATION_FORBIDDEN_FUNCTION),
    ("SELECT pg_read_file('/etc/passwd')", ErrorCode.VALIDATION_FORBIDDEN_FUNCTION),
    ("SELECT nextval('s')", ErrorCode.VALIDATION_FORBIDDEN_FUNCTION),
    ("SELECT dblink('x', 'y')", ErrorCode.VALIDATION_FORBIDDEN_FUNCTION),
    ("COPY t TO '/tmp/x'", None),
    ("SET work_mem = '1GB'", None),
    ("VACUUM", None),
    ("DO $$ BEGIN PERFORM 1; END $$", None),
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_allowed_queries_pass(sql):
    gate.validate(sql)  # must not raise


@pytest.mark.parametrize("sql,expected_code", REJECTED)
def test_rejected_queries_raise(sql, expected_code):
    with pytest.raises(McpError) as exc:
        gate.validate(sql)
    if expected_code is not None:
        assert exc.value.code == expected_code


def test_config_can_extend_but_not_shrink_denylist():
    g = ReadOnlyGate(extra_denied_functions=["my_custom_writer"])
    with pytest.raises(McpError):
        g.validate("SELECT my_custom_writer()")
    # built-ins remain denied
    with pytest.raises(McpError):
        g.validate("SELECT pg_sleep(1)")
