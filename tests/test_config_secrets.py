"""Config + secrets loader tests (ticket 02, pure unit seam)."""

import pytest

from pg_mcp.app import App
from pg_mcp.config import ConfigError, load_config
from pg_mcp.secrets import SecretResolutionError, resolve_secret

VALID = """
audit:
  log_path: {audit}
envs:
  local:
    host: localhost
    database: appdb
    user: ro
    secrets:
      provider: env
      password_key: LOCAL_PG
  prod:
    host: prod
    database: appdb
    user: ro
    protected: true
    secrets:
      provider: env
      password_key: PROD_PG
"""


def _write(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(text)
    return p


def test_valid_config_loads(tmp_path):
    cfg = load_config(_write(tmp_path, VALID.format(audit=tmp_path / "a.log")))
    assert set(cfg.envs) == {"local", "prod"}
    assert cfg.envs["prod"].protected is True
    assert cfg.envs["local"].statement_timeout_ms == 30_000  # default


def test_missing_file_is_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.yaml")


def test_unknown_field_is_schema_error(tmp_path):
    bad = _write(tmp_path, VALID.format(audit="a.log") + "  extra_env_typo: 1\n")
    with pytest.raises(ConfigError):
        load_config(bad)


def test_no_envs_is_error(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, "envs: {}\n"))


def test_max_rows_ceiling_enforced(tmp_path):
    cfg = load_config(_write(tmp_path, VALID.format(audit=tmp_path / "a.log")))
    cfg.envs["local"].max_rows = 999999
    assert cfg.envs["local"].effective_max_rows() == 10_000


def test_env_provider_resolves(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_PG", "hunter2")
    cfg = load_config(_write(tmp_path, VALID.format(audit=tmp_path / "a.log")))
    assert resolve_secret(cfg.envs["local"].secrets) == "hunter2"


def test_missing_secret_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("LOCAL_PG", raising=False)
    cfg = load_config(_write(tmp_path, VALID.format(audit=tmp_path / "a.log")))
    with pytest.raises(SecretResolutionError):
        resolve_secret(cfg.envs["local"].secrets)


def test_degraded_env_boots_but_is_flagged(tmp_path, monkeypatch):
    # local env var present, prod env var absent -> prod degraded, server still builds.
    monkeypatch.setenv("LOCAL_PG", "x")
    monkeypatch.delenv("PROD_PG", raising=False)
    cfg = load_config(_write(tmp_path, VALID.format(audit=tmp_path / "audit.log")))
    app = App(cfg)
    assert app.get_env("local").degraded is False
    assert app.get_env("prod").degraded is True
    assert app.get_env("prod").degraded_reason
