"""CLI entrypoint and transport selection (SPEC.md §8, tickets 01/02/08).

One codebase; transport chosen at startup via ``--transport`` / ``MCP_TRANSPORT``
(default stdio). HTTP mode serves stateless Streamable HTTP plus a ``/health`` endpoint.
``validate-config`` dry-runs schema validation + secret resolution.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .app import App
from .config import ConfigError, load_config
from .secrets import SecretResolutionError, resolve_secret
from .server import build_server

log = logging.getLogger("pg_mcp")


def _configure_logging() -> None:
    # Operational logs go to stderr — stdout is reserved for the stdio transport.
    logging.basicConfig(
        level=os.environ.get("PG_MCP_LOG_LEVEL", "INFO"),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _cmd_validate_config(path: str) -> int:
    try:
        cfg = load_config(path)
    except ConfigError as e:
        print(f"CONFIG INVALID: {e}", file=sys.stderr)
        return 1
    print(f"Config schema OK: {len(cfg.envs)} environment(s).")
    ok = True
    for name, env in cfg.envs.items():
        try:
            resolve_secret(env.secrets)
            print(f"  [ok]       {name}: secret resolvable ({env.secrets.provider})")
        except SecretResolutionError as e:
            ok = False
            print(f"  [degraded] {name}: {e}")
    print("All secrets resolvable." if ok else "Some environments would start DEGRADED.")
    return 0


def _run_server(path: str, transport: str) -> int:
    try:
        cfg = load_config(path)
    except ConfigError as e:
        print(f"CONFIG INVALID: {e}", file=sys.stderr)
        return 1
    app = App(cfg)
    app.transport = transport
    mcp = build_server(app)

    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.settings.host = os.environ.get("MCP_HOST", "0.0.0.0")
        mcp.settings.port = int(os.environ.get("MCP_PORT", "8000"))
        mcp.settings.stateless_http = True
        _add_health(mcp)
        mcp.run(transport="streamable-http")
    return 0


def _add_health(mcp) -> None:
    """Attach a /health route to the Streamable HTTP app."""
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def health(_request):
        return JSONResponse({"status": "ok"})

    try:
        mcp._custom_starlette_routes.append(Route("/health", health))  # type: ignore[attr-defined]
    except AttributeError:
        # Older/newer SDK: fall back to registering via the settings, best-effort.
        log.warning("could not attach /health route on this SDK version")


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    # Load a local .env into the process environment (no-op if absent) so the `env`
    # secrets provider can pick up locally-managed passwords. Real deployments use
    # exported env vars or the `gcp` provider.
    from dotenv import load_dotenv

    load_dotenv(os.environ.get("ENV_FILE", ".env"))
    parser = argparse.ArgumentParser(prog="pg-mcp", description="Read-only Postgres MCP server")
    parser.add_argument(
        "--config", default=os.environ.get("CONFIG_PATH"), help="path to YAML config"
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default=os.environ.get("MCP_TRANSPORT", "stdio"),
        help="transport (default stdio)",
    )
    sub = parser.add_subparsers(dest="command")
    vc = sub.add_parser("validate-config", help="validate config + secret resolution and exit")
    vc.add_argument("path", nargs="?", help="config path (defaults to --config)")

    args = parser.parse_args(argv)

    if args.command == "validate-config":
        path = args.path or args.config
        if not path:
            print("validate-config needs a path (arg or --config/CONFIG_PATH)", file=sys.stderr)
            return 2
        return _cmd_validate_config(path)

    if not args.config:
        print("--config or CONFIG_PATH is required", file=sys.stderr)
        return 2
    return _run_server(args.config, "stdio" if args.transport == "stdio" else "http")


if __name__ == "__main__":
    raise SystemExit(main())
