"""Diagnostic CLI for the allowlisted MCP client.

Commands never execute bid generation or write bid artifacts. ``call`` is
restricted to tools declared in ``mcp_servers.json``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any, Sequence

from .config import McpClientConfig, McpClientConfigError
from .manager import McpClientError, McpClientManager

_MAX_PRINT_CHARS = 20_000


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.mcp_client",
        description="Allowlisted MCP client diagnostics",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="override MCP_CLIENT_CONFIG (default: ./mcp_servers.json)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="list configured servers and tools")

    subparsers.add_parser(
        "check", help="connect and verify allowlisted tools (read-only)"
    )

    call = subparsers.add_parser(
        "call", help="call one configured tool with a JSON argument object"
    )
    call.add_argument("server")
    call.add_argument("tool")
    call.add_argument("arguments", nargs="?", default="{}")
    return parser


def _load_config(args: argparse.Namespace) -> McpClientConfig:
    return McpClientConfig.from_env(config_path=args.config)


def _print_config(config: McpClientConfig) -> None:
    print(f"config: {config.config_path}")
    print(f"enabled: {'true' if config.enabled else 'false'}")
    if not config.servers:
        print("servers: none")
        return
    for name, server in config.servers.items():
        print(f"server {name} ({server.transport})")
        for tool in server.allowed_tools:
            extra = ""
            if tool.purpose == "figure" and tool.figure_kinds:
                extra = " kinds=" + ",".join(tool.figure_kinds)
            print(
                f"  {tool.name}\tpurpose={tool.purpose}"
                f"\tmax_chars={tool.max_chars}{extra}"
            )


async def _run_check(config: McpClientConfig) -> int:
    if not config.servers:
        print("no configured MCP servers")
        return 0
    manager = McpClientManager(config)
    failures = 0
    try:
        await manager.start()
        seen_warnings = set(manager.warnings)
        for warning in manager.warnings:
            print(f"warning: {warning}")
        listed = await manager.list_server_tools()
        for warning in manager.warnings:
            if warning not in seen_warnings:
                print(f"warning: {warning}")
        for name, server in config.servers.items():
            if name not in manager.connected_servers:
                failures += 1
                print(f"{name}\t<connection>\tfailed")
                continue
            available = set(listed.get(name, []))
            for tool in server.allowed_tools:
                status = "ok" if tool.name in available else "missing"
                if status != "ok":
                    failures += 1
                print(f"{name}\t{tool.name}\t{status}")
    finally:
        await manager.aclose()
    if not config.enabled:
        print("note: MCP client is disabled; bid jobs will not use these servers")
    return 0 if failures == 0 else 1


def _print_result(result: Any) -> None:
    for block in list(getattr(result, "content", None) or []):
        block_type = getattr(block, "type", "")
        if block_type == "text":
            text = str(getattr(block, "text", "") or "")
            print(text[:_MAX_PRINT_CHARS])
        elif block_type == "image":
            data = str(getattr(block, "data", "") or "")
            mime = str(getattr(block, "mimeType", "") or "unknown")
            print(f"[image {mime}, base64_chars={len(data)}]")
        else:
            print(f"[{block_type or 'content'} omitted]")
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        print(json.dumps(structured, ensure_ascii=False, indent=2)[: _MAX_PRINT_CHARS])


async def _run_call(
    config: McpClientConfig,
    server: str,
    tool: str,
    arguments: dict[str, Any],
) -> int:
    manager = McpClientManager(config)
    try:
        await manager.start()
        for warning in manager.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        result = await manager.call_configured_tool(server, tool, arguments)
        _print_result(result)
        return 0
    finally:
        await manager.aclose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        config = _load_config(args)
    except McpClientConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.command == "list":
        _print_config(config)
        return 0
    if args.command == "check":
        try:
            return asyncio.run(_run_check(config))
        except McpClientError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    if args.command == "call":
        try:
            parsed = json.loads(args.arguments or "{}")
        except json.JSONDecodeError as exc:
            print(f"error: arguments must be valid JSON: {exc}", file=sys.stderr)
            return 2
        if not isinstance(parsed, dict):
            print("error: arguments must be a JSON object", file=sys.stderr)
            return 2
        try:
            return asyncio.run(
                _run_call(config, args.server, args.tool, parsed)
            )
        except McpClientError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    parser.print_help()
    return 2


__all__ = ["main"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
