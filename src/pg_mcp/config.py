"""Environment configuration model and loader (SPEC.md §2, ADR 0001).

Config is YAML. Passwords never appear here — only a ``secrets`` reference resolved by a
provider. Schema-invalid config fails hard at load; unresolvable secrets do NOT fail here
(they surface as a Degraded Environment at startup — see ``app.build_environments``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

DEFAULT_STATEMENT_TIMEOUT_MS = 30_000
DEFAULT_MAX_ROWS = 1_000
MAX_ROWS_CEILING = 10_000


class ConfigError(Exception):
    """Raised when the config file is missing or schema-invalid (hard startup failure)."""


class SecretsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["env", "gcp"]
    password_key: str
    # Meaning depends on provider:
    #   env -> unused (secret read from the env var named by password_key)
    #   gcp -> Secret Manager resource name, e.g. "pg-prod-password" or a
    #          fully-qualified "projects/<project>/secrets/<name>/versions/latest"
    path: str = ""
    # gcp only: GCP project id, used when 'path' is not fully qualified.
    project: str = ""


class EnvConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str
    port: int = 5432
    database: str
    user: str
    protected: bool = False
    allow_unconfirmed: bool = False
    statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS
    max_rows: int = DEFAULT_MAX_ROWS
    secrets: SecretsConfig

    def effective_max_rows(self) -> int:
        return min(self.max_rows, MAX_ROWS_CEILING)


class AuditConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    log_path: str = "./audit.log"


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    envs: dict[str, EnvConfig]
    audit: AuditConfig = Field(default_factory=AuditConfig)
    # Extra function names to reject in the gate (extends, never shrinks, the built-in list).
    extra_denied_functions: list[str] = Field(default_factory=list)


def load_config(path: str | Path) -> Config:
    """Load and schema-validate the YAML config. Raises ConfigError on any problem."""
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"config file not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML in {p}: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"config root must be a mapping, got {type(raw).__name__}")
    try:
        cfg = Config.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(f"config schema invalid:\n{e}") from e
    if not cfg.envs:
        raise ConfigError("config must define at least one environment under 'envs'")
    return cfg
