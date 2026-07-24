"""Secrets providers (SPEC.md §2, ADR 0001).

Each Environment declares its own provider. A provider resolves a reference into a live
secret value. Resolution failures raise ``SecretResolutionError`` — the caller decides
whether that means "hard fail" (schema) or "degrade this env" (missing secret).

Two providers are supported: ``env`` (read from the process environment) and ``gcp``
(Google Cloud Secret Manager). No other providers are offered.
"""

from __future__ import annotations

import os
from typing import Protocol

from .config import SecretsConfig


class SecretResolutionError(Exception):
    """Raised when a provider cannot resolve the referenced secret."""


class SecretsProvider(Protocol):
    def resolve(self, spec: SecretsConfig) -> str: ...


class EnvProvider:
    """Reads ``password_key`` directly from the process environment."""

    def resolve(self, spec: SecretsConfig) -> str:
        val = os.environ.get(spec.password_key)
        if not val:
            raise SecretResolutionError(f"environment variable {spec.password_key!r} is not set")
        return val


class GcpSecretManagerProvider:
    """Reads a secret version from Google Cloud Secret Manager.

    ``spec.path`` is the secret resource name. Either a fully-qualified
    ``projects/<project>/secrets/<name>/versions/<version>`` or a short
    ``<name>`` / ``<name>/versions/<version>`` resolved against ``spec.project``.
    """

    def resolve(self, spec: SecretsConfig) -> str:
        try:
            from google.cloud import secretmanager
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise SecretResolutionError(
                "google-cloud-secret-manager is not installed; "
                "install the 'gcp' extra to use the gcp provider"
            ) from exc

        name = self._resource_name(spec)
        try:
            client = secretmanager.SecretManagerServiceClient()
            response = client.access_secret_version(name=name)
        except Exception as exc:  # noqa: BLE001 - surface any client/API failure uniformly
            raise SecretResolutionError(f"gcp secret {name!r} unavailable: {exc}") from exc
        return response.payload.data.decode("utf-8")

    @staticmethod
    def _resource_name(spec: SecretsConfig) -> str:
        path = spec.path
        if path.startswith("projects/"):
            return path
        if not spec.project:
            raise SecretResolutionError(
                f"gcp secret {path!r} needs a 'project' (or a fully-qualified path)"
            )
        if "/versions/" not in path:
            path = f"{path}/versions/latest"
        return f"projects/{spec.project}/secrets/{path}"


_REGISTRY: dict[str, SecretsProvider] = {
    "env": EnvProvider(),
    "gcp": GcpSecretManagerProvider(),
}


def register_provider(name: str, provider: SecretsProvider) -> None:
    _REGISTRY[name] = provider


def resolve_secret(spec: SecretsConfig) -> str:
    """Resolve a secret via its declared provider. Raises SecretResolutionError."""
    provider = _REGISTRY.get(spec.provider)
    if provider is None:  # pragma: no cover - guarded by config Literal
        raise SecretResolutionError(f"unknown secrets provider: {spec.provider}")
    return provider.resolve(spec)
