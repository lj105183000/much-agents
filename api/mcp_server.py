"""MCP server exposing a safe subset of the Much Agents HTTP API.

The server is intentionally a thin proxy: every tool calls the local FastAPI
service, so existing guards, revision checks, artifact allowlists and profile
exclusions remain the single source of truth. It never reads or writes run
files directly.

Run over stdio (default)::

    uv run python -m api.mcp_server

Run over Streamable HTTP on loopback::

    uv run python -m api.mcp_server --transport http --host 127.0.0.1 --port 8765

Binding a non-loopback address requires ``MCP_HTTP_TOKEN``; clients must then
send ``Authorization: Bearer <token>``.
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import ipaddress
import json
import logging
import os
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal, Sequence
from urllib.parse import quote, urlsplit

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .artifacts import BID_PATTERNS

logger = logging.getLogger(__name__)

DEFAULT_API_BASE_URL = "http://127.0.0.1:8001"
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8765
MAX_ARTIFACT_CHARS = 200_000

_TEXT_CONTENT_TYPES = {
    "application/json",
    "application/x-ndjson",
    "text/csv",
}


class McpApiError(RuntimeError):
    """An API call failed or was rejected."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail

    def __str__(self) -> str:
        if self.status:
            return f"API {self.status}: {self.detail}"
        return self.detail


@dataclass(frozen=True)
class McpServerSettings:
    api_base_url: str = DEFAULT_API_BASE_URL
    http_host: str = DEFAULT_HTTP_HOST
    http_port: int = DEFAULT_HTTP_PORT
    http_token: str = ""
    api_token: str = ""

    @classmethod
    def from_env(cls) -> "McpServerSettings":
        base_url = (
            os.getenv("MCP_API_BASE_URL", DEFAULT_API_BASE_URL).strip()
            or DEFAULT_API_BASE_URL
        )
        _validate_api_base_url(base_url)
        host = (
            os.getenv("MCP_HTTP_HOST", DEFAULT_HTTP_HOST).strip()
            or DEFAULT_HTTP_HOST
        )
        raw_port = os.getenv("MCP_HTTP_PORT", str(DEFAULT_HTTP_PORT)).strip()
        try:
            port = int(raw_port or DEFAULT_HTTP_PORT)
        except ValueError as exc:
            raise ValueError("MCP_HTTP_PORT must be an integer") from exc
        if not 1 <= port <= 65535:
            raise ValueError("MCP_HTTP_PORT must be between 1 and 65535")
        return cls(
            api_base_url=base_url.rstrip("/"),
            http_host=host,
            http_port=port,
            http_token=os.getenv("MCP_HTTP_TOKEN", "").strip(),
            api_token=os.getenv("MCP_API_TOKEN", "").strip(),
        )


def _validate_api_base_url(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("MCP_API_BASE_URL must be an http(s) URL")
    if parsed.query or parsed.fragment:
        raise ValueError("MCP_API_BASE_URL must not contain a query or fragment")
    if parsed.path not in {"", "/"}:
        raise ValueError("MCP_API_BASE_URL must not include a path")


def _is_loopback(host: str) -> bool:
    name = host.strip().strip("[]").lower()
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def validate_http_binding(host: str, token: str) -> None:
    if not _is_loopback(host) and not token.strip():
        raise ValueError(
            "MCP_HTTP_TOKEN is required when binding a non-loopback MCP HTTP host"
        )


def _api_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        text = (response.text or "").strip()
        return (text or response.reason_phrase or "request failed")[:2000]
    if isinstance(body, dict):
        detail = body.get("detail") or body.get("error") or body
    else:
        detail = body
    if isinstance(detail, (dict, list)):
        try:
            return json.dumps(detail, ensure_ascii=False)[:2000]
        except (TypeError, ValueError):
            return str(detail)[:2000]
    return str(detail)[:2000]


class ApiProxy:
    """Fixed-endpoint HTTP client for the local Much Agents API."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
        api_token: str = "",
    ) -> None:
        _validate_api_base_url(base_url)
        self.base_url = base_url.rstrip("/")
        self._api_token = api_token.strip()
        headers = (
            {"Authorization": f"Bearer {self._api_token}"}
            if self._api_token
            else None
        )
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
            headers=headers,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> httpx.Response:
        try:
            response = await self._client.request(
                method,
                path,
                params=params,
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise McpApiError(
                0,
                f"cannot reach Much Agents API at {self.base_url}: {exc}",
            ) from exc
        if response.status_code >= 400:
            raise McpApiError(response.status_code, _api_detail(response))
        return response

    async def get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        response = await self._send("GET", path, params=params)
        return self._decode(response)

    async def post_json(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        response = await self._send("POST", path, payload=payload)
        return self._decode(response, allow_empty=True)

    async def patch_json(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> Any:
        response = await self._send("PATCH", path, payload=payload)
        return self._decode(response)

    async def get_text(self, path: str) -> tuple[str, str]:
        response = await self._send("GET", path)
        content_type = (
            response.headers.get("content-type", "").split(";")[0].strip().lower()
        )
        if content_type and not (
            content_type.startswith("text/")
            or content_type in _TEXT_CONTENT_TYPES
        ):
            raise McpApiError(
                415,
                f"artifact is not text ({content_type or 'unknown content type'})",
            )
        return response.text, content_type

    @staticmethod
    def _decode(response: httpx.Response, *, allow_empty: bool = False) -> Any:
        if response.status_code == 204 or not response.content:
            if allow_empty:
                return {}
            raise McpApiError(502, "API returned an empty response")
        try:
            return response.json()
        except ValueError as exc:
            raise McpApiError(502, "API returned invalid JSON") from exc


def _segment(value: str) -> str:
    return quote(str(value), safe="")


def _artifact_path(value: str) -> str:
    raw = (value or "").strip()
    if not raw or "\\" in raw:
        raise McpApiError(400, "invalid artifact path")
    candidate = PurePosixPath(raw)
    if candidate.is_absolute() or any(
        part in {"", ".", ".."} for part in candidate.parts
    ):
        raise McpApiError(400, "invalid artifact path")
    normalized = candidate.as_posix()
    if candidate.name == "profile.json":
        raise McpApiError(404, "artifact is not available")
    if not any(
        PurePosixPath(normalized).match(pattern) for pattern in BID_PATTERNS
    ):
        raise McpApiError(404, "artifact is not available")
    return normalized


def _run_artifact_base(doc_id: str, run_id: str) -> str:
    return (
        f"/api/documents/{_segment(doc_id)}"
        f"/bids/{_segment(run_id)}/artifacts"
    )


class BearerTokenMiddleware:
    """Require a static bearer token on every HTTP request."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self._expected = f"Bearer {token}".encode("latin-1")

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        authorization = b""
        for key, value in scope.get("headers", []):
            if key.lower() == b"authorization":
                authorization = value
                break
        if not authorization or not hmac.compare_digest(
            authorization, self._expected
        ):
            response = JSONResponse(
                {"detail": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="much-agents-mcp"'},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def build_http_app(server: FastMCP, token: str = "") -> ASGIApp:
    app: ASGIApp = server.streamable_http_app()
    if token.strip():
        app = BearerTokenMiddleware(app, token.strip())
    return app


def _http_security(host: str) -> TransportSecuritySettings | None:
    if _is_loopback(host):
        return None
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            host,
            f"{host}:*",
            "127.0.0.1:*",
            "localhost:*",
            "[::1]:*",
        ],
        allowed_origins=[
            f"http://{host}",
            f"https://{host}",
            f"http://{host}:*",
            f"https://{host}:*",
            "http://127.0.0.1:*",
            "http://localhost:*",
            "http://[::1]:*",
        ],
    )


def create_server(
    settings: McpServerSettings | None = None,
    *,
    proxy: ApiProxy | None = None,
    transport_security: TransportSecuritySettings | None = None,
) -> FastMCP:
    settings = settings or McpServerSettings.from_env()
    api_proxy = proxy or ApiProxy(
        settings.api_base_url,
        api_token=settings.api_token,
    )
    server = FastMCP(
        "much-agents",
        instructions=(
            "Much Agents 多 Agent 标书编写系统的本地 MCP 服务。"
            "所有工具都代理本地 FastAPI；长任务只返回 job_id，请用 get_job 轮询。"
            "投标人完整资料（profile.json）不会通过任何工具返回。"
        ),
        host=settings.http_host,
        port=settings.http_port,
        transport_security=transport_security,
    )

    @server.tool()
    async def health() -> dict[str, Any]:
        """读取 API 健康状态。"""
        return await api_proxy.get_json("/api/health")

    @server.tool()
    async def list_documents() -> dict[str, Any]:
        """列出已解析的招标文件及其投标 run。"""
        return await api_proxy.get_json("/api/documents")

    @server.tool()
    async def search_tender(
        query: str,
        doc_id: str | None = None,
        top_k: int = 5,
    ) -> dict[str, Any]:
        """检索招标文件片段；auto 模式优先语义检索，失败时回退关键词。"""
        payload: dict[str, Any] = {
            "query": query,
            "top_k": max(1, min(int(top_k), 30)),
            "mode": "auto",
        }
        if doc_id:
            payload["doc_id"] = doc_id
        return await api_proxy.post_json("/api/search", payload)

    @server.tool()
    async def list_companies(limit: int = 50) -> list[dict[str, Any]]:
        """列出投标人公司；仅返回 id、名称与统一社会信用代码。"""
        response = await api_proxy.get_json("/api/companies")
        companies = response.get("companies", []) if isinstance(response, dict) else []
        rows = [
            {
                "id": item.get("id"),
                "company_name": item.get("company_name"),
                "credit_code": item.get("credit_code"),
            }
            for item in companies
            if isinstance(item, dict)
        ]
        return rows[: max(1, min(int(limit), 200))]

    @server.tool()
    async def list_bid_runs(
        doc_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """列出服务端持久化投标 run。"""
        params: dict[str, Any] = {"limit": max(1, min(int(limit), 100))}
        if doc_id:
            params["doc_id"] = doc_id
        if status:
            params["status"] = status
        return await api_proxy.get_json("/api/bid-runs", params=params)

    @server.tool()
    async def get_bid_run(run_id: str) -> dict[str, Any]:
        """读取 run 状态、进度、门禁状态与缺失资料标签。"""
        return await api_proxy.get_json(
            f"/api/bid-runs/{_segment(run_id)}"
        )

    @server.tool()
    async def get_bid_outline(run_id: str) -> dict[str, Any]:
        """读取 run 的可编辑大纲与章节状态。"""
        return await api_proxy.get_json(
            f"/api/bid-runs/{_segment(run_id)}/outline"
        )

    @server.tool()
    async def get_bid_section(run_id: str, section_id: str) -> dict[str, Any]:
        """读取单个章节的最终正文、来源、问题与警告。"""
        return await api_proxy.get_json(
            f"/api/bid-runs/{_segment(run_id)}"
            f"/sections/{_segment(section_id)}"
        )

    @server.tool()
    async def get_bid_gate(run_id: str) -> dict[str, Any]:
        """读取最后一次组装生成的门禁结果。"""
        return await api_proxy.get_json(
            f"/api/bid-runs/{_segment(run_id)}/gate"
        )

    @server.tool()
    async def get_compliance(run_id: str) -> dict[str, Any]:
        """读取 21 项合规检查与一致性台账。"""
        return await api_proxy.get_json(
            f"/api/bid-runs/{_segment(run_id)}/compliance"
        )

    @server.tool()
    async def get_consistency(run_id: str) -> dict[str, Any]:
        """读取跨章节事实一致性台账。"""
        return await api_proxy.get_json(
            f"/api/bid-runs/{_segment(run_id)}/consistency"
        )

    @server.tool()
    async def get_job(job_id: str) -> dict[str, Any]:
        """读取解析或标书长任务状态；用于轮询 create/generate 返回的 job_id。"""
        return await api_proxy.get_json(f"/api/jobs/{_segment(job_id)}")

    @server.tool()
    async def list_artifacts(run_id: str) -> dict[str, Any]:
        """列出 run 的受控产物；profile.json 永不出现在结果中。"""
        run = await api_proxy.get_json(f"/api/bid-runs/{_segment(run_id)}")
        doc_id = str(run.get("doc_id") or "")
        if not doc_id:
            raise McpApiError(502, "API returned a run without doc_id")
        response = await api_proxy.get_json(
            _run_artifact_base(doc_id, run_id)
        )
        artifacts = (
            response.get("artifacts", []) if isinstance(response, dict) else []
        )
        safe = [
            item
            for item in artifacts
            if isinstance(item, dict)
            and PurePosixPath(str(item.get("path") or "")).name
            != "profile.json"
        ]
        if isinstance(response, dict):
            return {**response, "artifacts": safe}
        return {"artifacts": safe}

    @server.tool()
    async def read_artifact_text(run_id: str, path: str) -> dict[str, Any]:
        """按产物 allowlist 读取 Markdown/JSON/文本产物；profile.json 会被拒绝。"""
        normalized = _artifact_path(path)
        run = await api_proxy.get_json(f"/api/bid-runs/{_segment(run_id)}")
        doc_id = str(run.get("doc_id") or "")
        if not doc_id:
            raise McpApiError(502, "API returned a run without doc_id")
        base = _run_artifact_base(doc_id, run_id)
        text, content_type = await api_proxy.get_text(
            f"{base}/{quote(normalized, safe='/')}"
        )
        truncated = len(text) > MAX_ARTIFACT_CHARS
        return {
            "run_id": run_id,
            "path": normalized,
            "content_type": content_type or "text/plain",
            "truncated": truncated,
            "content": text[:MAX_ARTIFACT_CHARS],
        }

    @server.tool()
    async def list_bid_figures(run_id: str) -> dict[str, Any]:
        """读取已生成配图的清单、统计、跳过项与警告。"""
        return await api_proxy.get_json(
            f"/api/bid-runs/{_segment(run_id)}/figures"
        )

    @server.tool()
    async def create_bid_run(
        doc_id: str,
        company_id: str | None = None,
        max_sections: int | None = None,
    ) -> dict[str, Any]:
        """创建 run 与大纲；不接受内联 profile，资料由 company_id 载入。"""
        payload: dict[str, Any] = {"doc_id": doc_id}
        if company_id:
            payload["company_id"] = company_id
        if max_sections is not None:
            payload["max_sections"] = max_sections
        return await api_proxy.post_json("/api/bid-runs", payload)

    @server.tool()
    async def patch_bid_outline(
        run_id: str,
        revision: int,
        sections: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """用 revision 乐观锁修改 narrative 大纲，仅允许白名单字段。"""
        return await api_proxy.patch_json(
            f"/api/bid-runs/{_segment(run_id)}/outline",
            {"revision": revision, "sections": sections},
        )

    @server.tool()
    async def generate_bid_run(
        run_id: str,
        section_ids: list[str] | None = None,
        assemble: bool = True,
    ) -> dict[str, Any]:
        """入队生成；返回 job_id，请用 get_job 轮询。"""
        return await api_proxy.post_json(
            f"/api/bid-runs/{_segment(run_id)}/generate",
            {
                "section_ids": section_ids or [],
                "assemble": bool(assemble),
            },
        )

    @server.tool()
    async def resume_bid_run(run_id: str) -> dict[str, Any]:
        """续跑失败或未完成的章节；返回 job_id，请用 get_job 轮询。"""
        return await api_proxy.post_json(
            f"/api/bid-runs/{_segment(run_id)}/resume"
        )

    @server.tool()
    async def cancel_bid_run(run_id: str) -> dict[str, Any]:
        """请求取消；取消在章节边界生效。"""
        return await api_proxy.post_json(
            f"/api/bid-runs/{_segment(run_id)}/cancel"
        )

    @server.tool()
    async def assemble_bid_run(run_id: str) -> dict[str, Any]:
        """同步组装并重新生成配图、合规、一致性与门禁。"""
        return await api_proxy.post_json(
            f"/api/bid-runs/{_segment(run_id)}/assemble"
        )

    @server.tool()
    async def edit_bid_section(
        run_id: str,
        section_id: str,
        markdown: str,
    ) -> dict[str, Any]:
        """保存人工正文；等同人工编辑，会使旧门禁与报告失效。"""
        return await api_proxy.patch_json(
            f"/api/bid-runs/{_segment(run_id)}"
            f"/sections/{_segment(section_id)}",
            {"markdown": markdown},
        )

    @server.tool()
    async def regenerate_bid_section(
        run_id: str,
        section_id: str,
    ) -> dict[str, Any]:
        """单节重写；返回 job_id，旧稿保留至新稿完成。"""
        return await api_proxy.post_json(
            f"/api/bid-runs/{_segment(run_id)}"
            f"/sections/{_segment(section_id)}/regenerate"
        )

    @server.tool()
    async def plan_bid_fixes(
        run_id: str,
        check_ids: list[str] | None = None,
        max_items: int = 10,
    ) -> dict[str, Any]:
        """为未通过检查生成确定性/LLM 修复候选，不自动应用。"""
        return await api_proxy.post_json(
            f"/api/bid-runs/{_segment(run_id)}/compliance/fix-plan",
            {
                "check_ids": check_ids or [],
                "max_items": max(1, min(int(max_items), 30)),
            },
        )

    @server.tool()
    async def apply_bid_fix(
        run_id: str,
        section_id: str,
        find: str,
        replace: str,
    ) -> dict[str, Any]:
        """应用一处修复；find 必须恰好出现一次，应用后需重新组装。"""
        return await api_proxy.post_json(
            f"/api/bid-runs/{_segment(run_id)}/compliance/fixes/apply",
            {
                "section_id": section_id,
                "find": find,
                "replace": replace,
            },
        )

    @server.tool()
    async def regenerate_bid_figures(run_id: str) -> dict[str, Any]:
        """重新生成配图；会使旧门禁与报告失效，需要重新组装。"""
        return await api_proxy.post_json(
            f"/api/bid-runs/{_segment(run_id)}/figures/regenerate",
            {"force": True},
        )

    @server.tool()
    async def get_bid_profile_template() -> dict[str, Any]:
        """返回空白投标人资料模板，不返回任何公司实际资料。"""
        return await api_proxy.get_json("/api/bids/profile-template")

    setattr(server, "_much_agents_proxy", api_proxy)
    return server


def _run_http(
    server: FastMCP,
    *,
    host: str,
    port: int,
    token: str,
) -> None:
    import uvicorn

    uvicorn.run(
        build_http_app(server, token),
        host=host,
        port=port,
        log_level="info",
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Much Agents MCP server (thin proxy over the local API)",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "http", "streamable-http"),
        default="stdio",
        help="stdio (default) or Streamable HTTP",
    )
    parser.add_argument(
        "--host",
        default=None,
        help=f"MCP HTTP bind host (default: MCP_HTTP_HOST or {DEFAULT_HTTP_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"MCP HTTP port (default: MCP_HTTP_PORT or {DEFAULT_HTTP_PORT})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="List registered tools and exit",
    )
    args = parser.parse_args(argv)

    try:
        settings = McpServerSettings.from_env()
    except ValueError as exc:
        parser.error(str(exc))

    auth_enabled = os.getenv("AUTH_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    if auth_enabled and not settings.api_token:
        logger.warning(
            "MCP_API_TOKEN is not set; the API will reject MCP proxy calls "
            "with 401 while AUTH_ENABLED=true"
        )

    transport: Literal["stdio", "streamable-http"] = (
        "stdio" if args.transport == "stdio" else "streamable-http"
    )
    host = (args.host or settings.http_host).strip()
    port = args.port if args.port is not None else settings.http_port
    if not 1 <= port <= 65535:
        parser.error("--port must be between 1 and 65535")

    if transport != "stdio":
        try:
            validate_http_binding(host, settings.http_token)
        except ValueError as exc:
            parser.error(str(exc))

    security = _http_security(host) if transport != "stdio" else None
    server = create_server(settings, transport_security=security)

    if args.check:
        tools = asyncio.run(server.list_tools())
        print("Much Agents MCP server")
        print(f"API base: {settings.api_base_url}")
        print(f"Transport: {transport}")
        print(f"Tools ({len(tools)}):")
        for tool in sorted(tools, key=lambda item: item.name):
            print(f"  {tool.name}")
        return

    if transport == "stdio":
        server.run("stdio")
        return
    logger.info(
        "starting Much Agents MCP HTTP server on %s:%s (token=%s)",
        host,
        port,
        "set" if settings.http_token else "unset",
    )
    _run_http(
        server,
        host=host,
        port=port,
        token=settings.http_token,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
