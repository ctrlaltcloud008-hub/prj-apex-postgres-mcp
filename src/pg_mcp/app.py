"""Application assembly (SPEC.md §2, ADR 0001).

Builds the Environment set from config at startup: schema-invalid config already failed
in ``load_config``; here an unresolvable secret marks that Environment **Degraded**
(the server still boots and serves healthy envs) rather than crashing.
"""

from __future__ import annotations

import logging

from .audit import AuditLogger
from .config import Config
from .db import Environment
from .gate import ReadOnlyGate
from .secrets import SecretResolutionError, resolve_secret

log = logging.getLogger("pg_mcp")


class App:
    def __init__(self, config: Config):
        self.config = config
        self.transport = "stdio"
        self.gate = ReadOnlyGate(config.extra_denied_functions)
        self.audit = AuditLogger(config.audit.log_path)
        self.environments: dict[str, Environment] = {}
        self._build_environments()

    def _build_environments(self) -> None:
        for name, env_cfg in self.config.envs.items():
            reason: str | None = None
            try:
                resolve_secret(env_cfg.secrets)  # probe only; not cached
            except SecretResolutionError as e:
                reason = str(e)
                log.warning("Environment %r is DEGRADED at startup: %s", name, reason)
                self.audit.record_env_transition(name, transport="startup", reason=reason)
            self.environments[name] = Environment(name, env_cfg, degraded_reason=reason)

    def get_env(self, name: str) -> Environment | None:
        return self.environments.get(name)

    def close(self) -> None:
        for env in self.environments.values():
            env.close()
