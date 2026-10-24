"""Validated configuration for the allowlisted MCP client.

The client is an adapter for a small, operator-defined set of MCP tools. It is
never used by an LLM to discover or invoke arbitrary tools, and tool arguments
may only reference a fixed set of placeholders.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping
from urllib.parse import urlsplit

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.parsing.config import find_project_root

McpPurpose = Literal["evidence", "company", "figure"]
McpTransport = Literal["stdio", "streamable_http"]
ExternalFigureKind = Literal["site_plan", "process_flow"]

ALLOWED_PLACEHOLDERS = frozenset(
    {
        "query",
        "doc_id",
        "top_k",
        "company_name",
        "project_name",
        "section_id",
        "section_title",
        "figure_spec",
    }
)
_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_ENV_RE = _PLACEHOLDER_RE
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_MAX_CONFIG_BYTES = 1_000_000
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class McpClientConfigError(ValueError):
    """Raised when the MCP client configuration is invalid."""


def _expand_env(value: str, env: Mapping[str, str], *, where: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in env:
            raise McpClientConfigError(
                f"{where}: environment variable {name} is not set"
            )
        return str(env[name])

    return _ENV_RE.sub(replace, value)


def _expand_optional(value: Any, env: Mapping[str, str], *, where: str) -> Any:
    if isinstance(value, str):
        return _expand_env(value, env, where=where)
    if isinstance(value, list):
        return [
            _expand_optional(item, env, where=f"{where}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        return {
            str(key): _expand_optional(item, env, where=f"{where}.{key}")
            for key, item in value.items()
        }
    return value


def _expand_server_raw(
    name: str,
    server: Any,
    env: Mapping[str, str],
) -> Any:
    if not isinstance(server, dict):
        return server
    expanded = dict(server)
    for key in ("command", "cwd", "url"):
        if key in expanded:
            expanded[key] = _expand_optional(
                expanded[key], env, where=f"servers.{name}.{key}"
            )
    for key in ("args", "env", "headers"):
        if key in expanded:
            expanded[key] = _expand_optional(
                expanded[key], env, where=f"servers.{name}.{key}"
            )
    return expanded


def _placeholders(value: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(value, str):
        names.update(_PLACEHOLDER_RE.findall(value))
    elif isinstance(value, list):
        for item in value:
            names.update(_placeholders(item))
    elif isinstance(value, dict):
        for item in value.values():
            names.update(_placeholders(item))
    return names


class McpToolConfig(BaseModel):
    """One allowlisted tool and the fixed arguments used to call it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=128)
    purpose: McpPurpose
    arguments: dict[str, Any] = Field(default_factory=dict)
    max_chars: int = Field(default=4000, ge=100, le=50_000)
    figure_kinds: list[ExternalFigureKind] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _tool_name(cls, value: str) -> str:
        text = (value or "").strip()
        if not _TOOL_NAME_RE.fullmatch(text):
            raise ValueError("invalid MCP tool name")
        return text

    @field_validator("arguments")
    @classmethod
    def _known_placeholders(cls, value: dict[str, Any]) -> dict[str, Any]:
        unknown = _placeholders(value) - ALLOWED_PLACEHOLDERS
        if unknown:
            raise ValueError(
                "unknown MCP argument placeholders: " + ", ".join(sorted(unknown))
            )
        return value

    @model_validator(mode="after")
    def _figure_kinds_match_purpose(self) -> "McpToolConfig":
        if self.figure_kinds and self.purpose != "figure":
            raise ValueError("figure_kinds is only valid for purpose=figure")
        return self


class McpServerConfig(BaseModel):
    """One stdio or Streamable HTTP MCP server."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    transport: McpTransport = "stdio"
    command: str = Field(default="", max_length=1024)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str = Field(default="", max_length=4096)
    url: str = Field(default="", max_length=4096)
    headers: dict[str, str] = Field(default_factory=dict)
    allowed_tools: list[McpToolConfig] = Field(min_length=1, max_length=32)
    timeout_seconds: float = Field(default=30.0, ge=1.0, le=120.0)

    @field_validator("args")
    @classmethod
    def _args(cls, value: list[str]) -> list[str]:
        if len(value) > 64:
            raise ValueError("too many MCP server arguments")
        for item in value:
            if len(item) > 4096 or _CONTROL_RE.search(item):
                raise ValueError("invalid MCP server argument")
        return value

    @field_validator("env")
    @classmethod
    def _env(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 64:
            raise ValueError("too many MCP server environment variables")
        for key, item in value.items():
            if not key or "=" in key or "\x00" in key or "\x00" in item:
                raise ValueError("invalid MCP server environment variable")
        return value

    @field_validator("headers")
    @classmethod
    def _headers(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 32:
            raise ValueError("too many MCP HTTP headers")
        for key, item in value.items():
            if not key or _CONTROL_RE.search(key) or _CONTROL_RE.search(item):
                raise ValueError("invalid MCP HTTP header")
        return value

    @field_validator("allowed_tools")
    @classmethod
    def _unique_tools(cls, value: list[McpToolConfig]) -> list[McpToolConfig]:
        names = [tool.name for tool in value]
        if len(names) != len(set(names)):
            raise ValueError("duplicate MCP tool names on one server")
        return value

    @model_validator(mode="after")
    def _transport_fields(self) -> "McpServerConfig":
        if self.transport == "stdio":
            if not self.command.strip():
                raise ValueError("stdio MCP server requires command")
            if self.url or self.headers:
                raise ValueError("stdio MCP server must not set url or headers")
            return self
        if self.command or self.args or self.env or self.cwd:
            raise ValueError("HTTP MCP server must not set stdio fields")
        if not self.url:
            raise ValueError("HTTP MCP server requires url")
        parts = urlsplit(self.url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("MCP server url must be an absolute http(s) URL")
        if parts.fragment:
            raise ValueError("MCP server url must not contain a fragment")
        if parts.username or parts.password:
            raise ValueError("MCP server url must not embed credentials")
        return self


class _ConfigFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    servers: dict[str, McpServerConfig] = Field(default_factory=dict)

    @field_validator("servers")
    @classmethod
    def _server_names(
        cls, value: dict[str, McpServerConfig]
    ) -> dict[str, McpServerConfig]:
        for name in value:
            if not _NAME_RE.fullmatch(name):
                raise ValueError(f"invalid MCP server name: {name!r}")
        return value


@dataclass(frozen=True, slots=True)
class McpClientConfig:
    """Resolved client settings, including enabled state and safety limits."""

    enabled: bool
    config_path: Path
    servers: dict[str, McpServerConfig]
    max_servers: int = 8
    max_tools: int = 64
    max_chars: int = 4000
    max_image_bytes: int = 8_000_000
    max_image_dimension: int = 4000
    call_timeout_seconds: float = 30.0

    def tools_for(self, purpose: McpPurpose) -> list[tuple[str, McpToolConfig]]:
        return [
            (server_name, tool)
            for server_name, server in self.servers.items()
            for tool in server.allowed_tools
            if tool.purpose == purpose
        ]

    def has_tools(self, purpose: McpPurpose) -> bool:
        return bool(self.tools_for(purpose))

    @classmethod
    def from_env(
        cls,
        *,
        project_root: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        config_path: str | Path | None = None,
    ) -> "McpClientConfig":
        root = (
            Path(project_root).resolve()
            if project_root is not None
            else find_project_root()
        )
        if env is None:
            dotenv = root / ".env"
            if dotenv.is_file():
                load_dotenv(dotenv, override=False, encoding="utf-8")
            env = dict(os.environ)
        values = env

        raw_path = config_path if config_path is not None else values.get(
            "MCP_CLIENT_CONFIG", "./mcp_servers.json"
        )
        path_text = _expand_env(
            str(raw_path or "./mcp_servers.json"),
            values,
            where="MCP_CLIENT_CONFIG",
        )
        path = Path(path_text).expanduser()
        if not path.is_absolute():
            path = root / path
        path = path.resolve()

        explicit_enabled = values.get("MCP_CLIENT_ENABLED")
        file_enabled: bool | None = None
        servers: dict[str, McpServerConfig] = {}
        if path.is_file():
            if path.stat().st_size > _MAX_CONFIG_BYTES:
                raise McpClientConfigError("MCP client config file is too large")
            try:
                raw = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise McpClientConfigError(
                    f"could not read MCP client config {path.name}: {type(exc).__name__}"
                ) from exc
            if not isinstance(raw, dict):
                raise McpClientConfigError("MCP client config must be a JSON object")
            expanded = dict(raw)
            raw_servers = raw.get("servers", {})
            if isinstance(raw_servers, dict):
                expanded["servers"] = {
                    str(name): _expand_server_raw(str(name), server, values)
                    for name, server in raw_servers.items()
                }
            try:
                parsed = _ConfigFile.model_validate(expanded)
            except ValueError as exc:
                raise McpClientConfigError(
                    f"invalid MCP client config: {str(exc)[:600]}"
                ) from exc
            file_enabled = parsed.enabled
            servers = dict(parsed.servers)
        elif config_path is not None:
            raise McpClientConfigError(
                f"MCP client config does not exist: {path.name}"
            )

        if explicit_enabled is not None and str(explicit_enabled).strip():
            enabled = _env_bool(values, "MCP_CLIENT_ENABLED", False)
        else:
            enabled = bool(file_enabled)
        if enabled and not path.is_file():
            raise McpClientConfigError(
                "MCP_CLIENT_ENABLED is true but the client config does not exist"
            )
        if enabled and not servers:
            raise McpClientConfigError(
                "MCP_CLIENT_ENABLED is true but no MCP servers are configured"
            )

        max_servers = _env_int(values, "MCP_CLIENT_MAX_SERVERS", 8, 1, 32)
        max_tools = _env_int(values, "MCP_CLIENT_MAX_TOOLS", 64, 1, 256)
        max_chars = _env_int(values, "MCP_CLIENT_MAX_CHARS", 4000, 100, 50_000)
        max_image_bytes = _env_int(
            values, "MCP_CLIENT_MAX_IMAGE_BYTES", 8_000_000, 1024, 16_000_000
        )
        max_image_dimension = _env_int(
            values, "MCP_CLIENT_MAX_IMAGE_DIMENSION", 4000, 256, 8000
        )
        timeout = _env_float(values, "MCP_CLIENT_TIMEOUT_SECONDS", 30.0, 1.0, 120.0)

        if len(servers) > max_servers:
            raise McpClientConfigError(
                f"too many MCP servers: {len(servers)} > {max_servers}"
            )
        tool_count = sum(len(server.allowed_tools) for server in servers.values())
        if tool_count > max_tools:
            raise McpClientConfigError(
                f"too many MCP tools: {tool_count} > {max_tools}"
            )

        config_dir = path.parent
        resolved_servers: dict[str, McpServerConfig] = {}
        for name, server in servers.items():
            cwd = server.cwd
            if cwd and not Path(cwd).is_absolute():
                cwd = str(config_dir / cwd)
            resolved_servers[name] = server.model_copy(update={"cwd": cwd})

        return cls(
            enabled=enabled,
            config_path=path,
            servers=resolved_servers,
            max_servers=max_servers,
            max_tools=max_tools,
            max_chars=max_chars,
            max_image_bytes=max_image_bytes,
            max_image_dimension=max_image_dimension,
            call_timeout_seconds=timeout,
        )


def _env_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None or not str(raw).strip():
        return default
    value = str(raw).strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise McpClientConfigError(f"{key} must be a boolean")


def _env_int(
    env: Mapping[str, str],
    key: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = env.get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise McpClientConfigError(f"{key} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise McpClientConfigError(
            f"{key} must be between {minimum} and {maximum}"
        )
    return value


def _env_float(
    env: Mapping[str, str],
    key: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    raw = env.get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise McpClientConfigError(f"{key} must be a number") from exc
    if not minimum <= value <= maximum:
        raise McpClientConfigError(
            f"{key} must be between {minimum:g} and {maximum:g}"
        )
    return value


__all__ = [
    "ALLOWED_PLACEHOLDERS",
    "ExternalFigureKind",
    "McpClientConfig",
    "McpClientConfigError",
    "McpPurpose",
    "McpServerConfig",
    "McpToolConfig",
    "McpTransport",
]
