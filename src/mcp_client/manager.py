"""Allowlisted MCP client used for external evidence and optional drawings.

The LLM never chooses or invokes MCP tools. Only tools declared in
``mcp_servers.json`` are reachable, placeholder values are fixed by the
system, and every failure degrades to local retrieval or the built-in Pillow
renderer instead of blocking bid generation.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import os
import re
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from mcp import ClientSession, types
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client
from PIL import Image, UnidentifiedImageError

from src.bidding.models import Evidence

from .config import (
    McpClientConfig,
    McpClientConfigError,
    McpPurpose,
    McpServerConfig,
    McpToolConfig,
)

logger = logging.getLogger(__name__)

_EXTERNAL_FIGURE_KINDS = frozenset({"site_plan", "process_flow"})
_ALLOWED_IMAGE_MIMES = frozenset({"image/png", "image/jpeg"})
_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_EVIDENCE_TAG_RE = re.compile(r"</?EVIDENCE\b[^>]*>", re.IGNORECASE)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_IMAGE_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_MIME_ALIASES = {
    "image/jpg": "image/jpeg",
    "image/pjpeg": "image/jpeg",
    "image/x-png": "image/png",
}


class McpClientError(RuntimeError):
    """A configured MCP operation failed; callers should degrade locally."""


# Figure rendering runs in a worker thread, so external drawing is exposed as
# a synchronous callback bridged back to the manager's event loop.
ExternalRenderer = Callable[[dict[str, Any], Path], str | bool | None]


@dataclass(slots=True)
class _Connection:
    name: str
    server: McpServerConfig
    session: Any
    lock: asyncio.Lock


@dataclass(slots=True)
class _ExternalImage:
    data: bytes
    mime_type: str


SessionConnector = Callable[
    [str, McpServerConfig], AbstractAsyncContextManager[Any]
]


def _safe_error(exc: BaseException, limit: int = 180) -> str:
    text = " ".join(str(exc).split())
    return text[:limit] or type(exc).__name__


def _normalize_mime(value: str) -> str:
    mime = (value or "").split(";", 1)[0].strip().lower()
    return _MIME_ALIASES.get(mime, mime)


def _clean_external_text(value: str) -> str:
    text = _EVIDENCE_TAG_RE.sub(" ", value or "")
    text = _CONTROL_RE.sub(" ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _render_value(value: Any, values: dict[str, Any]) -> Any:
    if isinstance(value, str):
        matches = list(_PLACEHOLDER_RE.finditer(value))
        if not matches:
            return value
        if len(matches) == 1 and matches[0].span() == (0, len(value)):
            name = matches[0].group(1)
            if name not in values:
                raise McpClientError(f"no value for MCP placeholder {name}")
            return values[name]

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in values:
                raise McpClientError(f"no value for MCP placeholder {name}")
            return str(values[name])

        return _PLACEHOLDER_RE.sub(replace, value)
    if isinstance(value, list):
        return [_render_value(item, values) for item in value]
    if isinstance(value, dict):
        return {str(key): _render_value(item, values) for key, item in value.items()}
    return value


def render_arguments(
    template: dict[str, Any],
    values: dict[str, Any],
) -> dict[str, Any]:
    rendered = _render_value(template or {}, values)
    if not isinstance(rendered, dict):  # pragma: no cover - config guarantees dict
        raise McpClientError("MCP tool arguments must be an object")
    return rendered


def _extract_texts(result: Any) -> list[str]:
    texts: list[str] = []
    for block in list(getattr(result, "content", None) or []):
        if getattr(block, "type", "") == "text":
            text = str(getattr(block, "text", "") or "")
            if text.strip():
                texts.append(text)
    return texts


def _structured_urls(value: Any, depth: int = 0) -> list[str]:
    if depth > 3:
        return []
    urls: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if (
                str(key).lower() in {"url", "image_url", "image", "href"}
                and isinstance(item, str)
                and item.lower().startswith(("http://", "https://"))
            ):
                urls.append(item)
            else:
                urls.extend(_structured_urls(item, depth + 1))
    elif isinstance(value, list):
        for item in value:
            urls.extend(_structured_urls(item, depth + 1))
    return urls


@asynccontextmanager
async def _connect_server(
    name: str,
    server: McpServerConfig,
) -> Any:
    del name  # used by the injectable connector signature only
    read_timeout = timedelta(seconds=server.timeout_seconds)
    if server.transport == "stdio":
        params = StdioServerParameters(
            command=server.command,
            args=list(server.args),
            env=dict(server.env) or None,
            cwd=server.cwd or None,
        )
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=read_timeout,
            ) as session:
                await session.initialize()
                yield session
        return
    async with streamablehttp_client(
        server.url,
        headers=dict(server.headers) or None,
        timeout=server.timeout_seconds,
        sse_read_timeout=max(300.0, server.timeout_seconds * 4),
    ) as (read_stream, write_stream, _):
        async with ClientSession(
            read_stream,
            write_stream,
            read_timeout_seconds=read_timeout,
        ) as session:
            await session.initialize()
            yield session


class McpClientManager:
    """One job-scoped set of MCP sessions with allowlisted calls."""

    def __init__(
        self,
        config: McpClientConfig,
        *,
        connector: SessionConnector | None = None,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self.warnings: list[str] = []
        self._connector = connector or _connect_server
        self._connections: dict[str, _Connection] = {}
        self._stack: AsyncExitStack | None = None
        self._closed = False
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(config.call_timeout_seconds),
            follow_redirects=False,
            transport=http_transport,
        )

    async def __aenter__(self) -> "McpClientManager":
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    @property
    def connected_servers(self) -> list[str]:
        return list(self._connections)

    def has_tools(self, purpose: McpPurpose) -> bool:
        return self.config.has_tools(purpose)

    def _warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    async def start(self) -> None:
        """Connect configured servers; failed servers degrade independently."""
        if self._closed:
            raise McpClientError("MCP client manager is closed")
        if self._stack is not None:
            return
        stack = AsyncExitStack()
        self._stack = stack
        for name, server in self.config.servers.items():
            try:
                session = await stack.enter_async_context(
                    self._connector(name, server)
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - one server must not stop the job
                self._warn(f"MCP server {name} connection failed: {type(exc).__name__}")
                logger.warning(
                    "MCP server %s failed to connect: %s", name, _safe_error(exc)
                )
                continue
            self._connections[name] = _Connection(
                name=name,
                server=server,
                session=session,
                lock=asyncio.Lock(),
            )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._stack is not None:
                await self._stack.aclose()
                self._stack = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - shutdown is best-effort
            logger.warning("could not close MCP sessions: %s", _safe_error(exc))
        finally:
            self._connections.clear()
            try:
                await self._http.aclose()
            except Exception:  # noqa: BLE001
                logger.warning("could not close MCP HTTP client", exc_info=True)

    async def _call_raw(
        self,
        connection: _Connection,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        timeout = timedelta(seconds=connection.server.timeout_seconds)
        async with connection.lock:
            try:
                result = await connection.session.call_tool(
                    tool_name,
                    arguments,
                    read_timeout_seconds=timeout,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - no transport details leaked
                raise McpClientError(
                    f"{connection.name}/{tool_name}: {type(exc).__name__}"
                ) from exc
        if bool(getattr(result, "isError", False)):
            raise McpClientError(f"{connection.name}/{tool_name}: tool returned an error")
        return result

    async def _call_configured(
        self,
        server_name: str,
        tool: McpToolConfig,
        values: dict[str, Any],
    ) -> Any:
        connection = self._connections.get(server_name)
        if connection is None:
            raise McpClientError(f"MCP server is unavailable: {server_name}")
        arguments = render_arguments(tool.arguments, values)
        return await self._call_raw(connection, tool.name, arguments)

    async def collect_evidence(
        self,
        *,
        query: str,
        doc_id: str = "",
        top_k: int = 5,
        company_name: str = "",
        project_name: str = "",
        section_id: str = "",
        section_title: str = "",
    ) -> tuple[list[Evidence], list[str]]:
        """Call allowlisted evidence/company tools concurrently.

        Returns ``(evidence, warnings)``. Failures never raise; the caller keeps
        local retrieval results.
        """
        entries = [
            (server_name, tool)
            for server_name, server in self.config.servers.items()
            for tool in server.allowed_tools
            if tool.purpose in {"evidence", "company"}
        ]
        if not entries:
            return [], []
        values = {
            "query": query,
            "doc_id": doc_id,
            "top_k": int(top_k),
            "company_name": company_name,
            "project_name": project_name,
            "section_id": section_id,
            "section_title": section_title,
        }
        results = await asyncio.gather(
            *[
                self._collect_one(server_name, tool, values)
                for server_name, tool in entries
            ],
            return_exceptions=True,
        )
        evidence: list[Evidence] = []
        warnings: list[str] = []
        for (server_name, tool), result in zip(entries, results):
            if isinstance(result, Exception):
                warnings.append(
                    f"MCP evidence tool {server_name}/{tool.name} failed: "
                    f"{_safe_error(result)}"
                )
                logger.warning(
                    "MCP evidence tool %s/%s failed: %s",
                    server_name,
                    tool.name,
                    _safe_error(result),
                )
                continue
            text, warning = result
            if warning:
                warnings.append(warning)
            if not text:
                continue
            evidence.append(
                Evidence(
                    chunk_id=f"mcp:{server_name}:{tool.name}:{len(evidence) + 1}",
                    text=text,
                    section_path=f"MCP/{server_name}/{tool.name}",
                    doc_id="mcp",
                )
            )
        return evidence, list(dict.fromkeys(warnings))

    async def _collect_one(
        self,
        server_name: str,
        tool: McpToolConfig,
        values: dict[str, Any],
    ) -> tuple[str | None, str | None]:
        result = await self._call_configured(server_name, tool, values)
        texts = _extract_texts(result)
        body = _clean_external_text("\n\n".join(texts))
        if not body:
            return None, f"MCP evidence tool {server_name}/{tool.name} returned no text"
        limit = min(tool.max_chars, self.config.max_chars)
        prefix = f"【MCP 外部证据：{server_name}/{tool.name}；未经核实，仅作线索】\n"
        available = max(0, limit - len(prefix))
        if available <= 0:
            return None, f"MCP evidence tool {server_name}/{tool.name} is limited to 0 chars"
        return (prefix + body[:available])[:limit], None

    async def list_server_tools(self) -> dict[str, list[str]]:
        """List tools on connected servers (diagnostic use only)."""
        results: dict[str, list[str]] = {}
        for name, connection in self._connections.items():
            async with connection.lock:
                try:
                    response = await connection.session.list_tools()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    self._warn(
                        f"MCP server {name} tool listing failed: {type(exc).__name__}"
                    )
                    logger.warning(
                        "could not list tools for MCP server %s: %s",
                        name,
                        _safe_error(exc),
                    )
                    continue
            tools = list(getattr(response, "tools", None) or [])
            results[name] = [
                str(getattr(tool, "name", ""))
                for tool in tools
                if getattr(tool, "name", "")
            ]
        return results

    async def call_configured_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        """Call one configured tool with raw arguments (CLI diagnostics only)."""
        server = self.config.servers.get(server_name)
        if server is None:
            raise McpClientError(f"unknown MCP server: {server_name}")
        tool = next(
            (item for item in server.allowed_tools if item.name == tool_name),
            None,
        )
        if tool is None:
            raise McpClientError(f"MCP tool is not allowlisted: {server_name}/{tool_name}")
        connection = self._connections.get(server_name)
        if connection is None:
            raise McpClientError(f"MCP server is unavailable: {server_name}")
        if not isinstance(arguments, dict):
            raise McpClientError("MCP tool arguments must be a JSON object")
        return await self._call_raw(connection, tool.name, arguments)

    async def _collect_image_urls(self, result: Any) -> list[str]:
        urls: list[str] = list(_structured_urls(getattr(result, "structuredContent", None)))
        for text in _extract_texts(result):
            urls.extend(_IMAGE_URL_RE.findall(text))
        cleaned: list[str] = []
        for url in urls:
            text = url.rstrip(".,;:!?)]}")
            if text and text not in cleaned:
                cleaned.append(text)
        return cleaned[:3]

    async def _result_image(self, result: Any) -> _ExternalImage | None:
        for block in list(getattr(result, "content", None) or []):
            block_type = getattr(block, "type", "")
            if block_type == "image":
                mime = _normalize_mime(str(getattr(block, "mimeType", "")))
                data = str(getattr(block, "data", "") or "")
                if mime not in _ALLOWED_IMAGE_MIMES:
                    raise McpClientError(f"unsupported external image MIME: {mime or 'missing'}")
                if not data:
                    raise McpClientError("external image content is empty")
                return _ExternalImage(self._decode_base64(data), mime)
            if block_type == "resource_link":
                mime = _normalize_mime(str(getattr(block, "mimeType", "")))
                uri = str(getattr(block, "uri", "") or "")
                if mime in _ALLOWED_IMAGE_MIMES and uri:
                    return await self._download_image(uri)
            resource = getattr(block, "resource", None)
            if resource is not None:
                mime = _normalize_mime(str(getattr(resource, "mimeType", "")))
                blob = getattr(resource, "blob", None)
                if mime in _ALLOWED_IMAGE_MIMES and blob:
                    return _ExternalImage(self._decode_base64(str(blob)), mime)
                uri = str(getattr(resource, "uri", "") or "")
                if mime in _ALLOWED_IMAGE_MIMES and uri.startswith(("http://", "https://")):
                    return await self._download_image(uri)
        urls = await self._collect_image_urls(result)
        errors: list[str] = []
        for url in urls:
            try:
                return await self._download_image(url)
            except Exception as exc:  # noqa: BLE001 - try the next candidate URL
                errors.append(_safe_error(exc))
        if errors:
            raise McpClientError(errors[0])
        return None

    def _decode_base64(self, value: str) -> bytes:
        try:
            data = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise McpClientError("external image is not valid base64") from exc
        if not data:
            raise McpClientError("external image is empty")
        if len(data) > self.config.max_image_bytes:
            raise McpClientError("external image exceeds the configured size limit")
        return data

    async def _download_image(self, url: str) -> _ExternalImage:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise McpClientError("external image URL must use http(s)")
        if parts.username or parts.password:
            raise McpClientError("external image URL must not embed credentials")
        async with self._http.stream("GET", url) as response:
            if response.status_code != 200:
                raise McpClientError(
                    f"external image download failed: HTTP {response.status_code}"
                )
            mime = _normalize_mime(response.headers.get("content-type", ""))
            if mime not in _ALLOWED_IMAGE_MIMES:
                raise McpClientError(
                    f"unsupported external image content type: {mime or 'missing'}"
                )
            declared = response.headers.get("content-length")
            if declared:
                try:
                    if int(declared) > self.config.max_image_bytes:
                        raise McpClientError(
                            "external image exceeds the configured size limit"
                        )
                except ValueError:
                    pass
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > self.config.max_image_bytes:
                    raise McpClientError(
                        "external image exceeds the configured size limit"
                    )
        if not data:
            raise McpClientError("external image download is empty")
        return _ExternalImage(bytes(data), mime)

    def _save_image(self, image: _ExternalImage, target: Path) -> None:
        if image.mime_type not in _ALLOWED_IMAGE_MIMES:
            raise McpClientError("unsupported external image MIME")
        if not image.data:
            raise McpClientError("external image is empty")
        if len(image.data) > self.config.max_image_bytes:
            raise McpClientError("external image exceeds the configured size limit")
        try:
            with Image.open(BytesIO(image.data)) as source:
                width, height = source.size
                if (
                    width <= 0
                    or height <= 0
                    or width > self.config.max_image_dimension
                    or height > self.config.max_image_dimension
                ):
                    raise McpClientError("external image dimensions are not allowed")
                if source.format not in {"PNG", "JPEG"}:
                    raise McpClientError("external image must be PNG or JPEG")
                source.load()
                frame = source
                if frame.mode not in {"RGB", "RGBA", "L", "LA", "P"}:
                    frame = frame.convert("RGB")
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(f"{target.name}.{uuid4().hex}.tmp")
                try:
                    frame.save(temporary, format="PNG")
                    if temporary.stat().st_size > self.config.max_image_bytes:
                        raise McpClientError(
                            "external image exceeds the configured size limit"
                        )
                    os.replace(temporary, target)
                finally:
                    try:
                        temporary.unlink()
                    except FileNotFoundError:
                        pass
                    except OSError:
                        logger.warning(
                            "could not remove temporary external image %s",
                            temporary,
                            exc_info=True,
                        )
        except McpClientError:
            raise
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise McpClientError("external image cannot be decoded") from exc

    def _external_timeout(self) -> float:
        tools = len(self.config.tools_for("figure"))
        return min(
            120.0,
            self.config.call_timeout_seconds * max(1, tools) + 10.0,
        )

    async def render_external_figure(
        self,
        spec: dict[str, Any],
        target: str | Path,
    ) -> str:
        """Render one external schematic and return ``mcp:<server>/<tool>``."""
        kind = str((spec or {}).get("kind") or "")
        if kind not in _EXTERNAL_FIGURE_KINDS:
            raise McpClientError(
                f"external drawing is not allowed for figure kind: {kind or 'missing'}"
            )
        tools = [
            (server_name, tool)
            for server_name, tool in self.config.tools_for("figure")
            if not tool.figure_kinds or kind in tool.figure_kinds
        ]
        if not tools:
            raise McpClientError(f"no configured external drawing tool for {kind}")
        values = {
            "query": str(spec.get("title") or ""),
            "doc_id": str(spec.get("doc_id") or ""),
            "top_k": 5,
            "company_name": "",
            "project_name": str(spec.get("project_name") or ""),
            "section_id": str(spec.get("section_id") or ""),
            "section_title": str(spec.get("section_title") or ""),
            "figure_spec": spec,
        }
        target_path = Path(target)
        errors: list[str] = []
        try:
            async with asyncio.timeout(self._external_timeout()):
                for server_name, tool in tools:
                    try:
                        result = await self._call_configured(
                            server_name, tool, values
                        )
                        image = await self._result_image(result)
                        if image is None:
                            raise McpClientError("tool returned no image content")
                        self._save_image(image, target_path)
                        return f"mcp:{server_name}/{tool.name}"
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - try the next tool
                        errors.append(f"{server_name}/{tool.name}: {_safe_error(exc)}")
                        logger.warning(
                            "external figure tool %s/%s failed: %s",
                            server_name,
                            tool.name,
                            _safe_error(exc),
                        )
        except TimeoutError as exc:
            raise McpClientError("external drawing timed out") from exc
        detail = "; ".join(errors)[:400] if errors else "no drawing tool succeeded"
        raise McpClientError(detail)


async def open_mcp_manager(
    project_root: str | Path | None = None,
    *,
    env: dict[str, str] | None = None,
    config_path: str | Path | None = None,
) -> McpClientManager | None:
    """Open a job-scoped manager when the client is enabled."""
    config = McpClientConfig.from_env(
        project_root=project_root,
        env=env,
        config_path=config_path,
    )
    if not config.enabled:
        return None
    manager = McpClientManager(config)
    await manager.start()
    return manager


def threadsafe_external_renderer(
    manager: McpClientManager,
    loop: asyncio.AbstractEventLoop | None = None,
) -> ExternalRenderer:
    """Bridge async MCP drawing into the worker thread used by figure rendering."""
    target_loop = loop or asyncio.get_running_loop()
    tools = len(manager.config.tools_for("figure"))
    wait_seconds = min(
        160.0,
        manager.config.call_timeout_seconds * max(1, tools) + 20.0,
    )

    def renderer(spec: dict[str, Any], target: Path) -> str:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is target_loop:
            raise McpClientError(
                "external MCP renderer must run in a worker thread"
            )
        if target_loop.is_closed():
            raise McpClientError("MCP client event loop is closed")
        future = asyncio.run_coroutine_threadsafe(
            manager.render_external_figure(spec, Path(target)),
            target_loop,
        )
        try:
            return future.result(timeout=wait_seconds)
        except FutureTimeoutError:
            future.cancel()
            raise McpClientError("external MCP drawing timed out") from None

    return renderer


__all__ = [
    "ExternalRenderer",
    "McpClientConfigError",
    "McpClientError",
    "McpClientManager",
    "open_mcp_manager",
    "render_arguments",
    "threadsafe_external_renderer",
]
