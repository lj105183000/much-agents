from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError
from starlette.responses import Response

from api.mcp_server import (
    ApiProxy,
    BearerTokenMiddleware,
    McpServerSettings,
    _http_security,
    create_server,
    main,
    validate_http_binding,
)


EXPECTED_TOOLS = {
    "health",
    "list_documents",
    "search_tender",
    "list_companies",
    "list_bid_runs",
    "get_bid_run",
    "get_bid_outline",
    "get_bid_section",
    "get_bid_gate",
    "get_compliance",
    "get_consistency",
    "get_job",
    "list_artifacts",
    "read_artifact_text",
    "list_bid_figures",
    "create_bid_run",
    "patch_bid_outline",
    "generate_bid_run",
    "resume_bid_run",
    "cancel_bid_run",
    "assemble_bid_run",
    "edit_bid_section",
    "regenerate_bid_section",
    "plan_bid_fixes",
    "apply_bid_fix",
    "regenerate_bid_figures",
    "get_bid_profile_template",
}


class Recorder:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "GET" and path == "/api/health":
            return httpx.Response(
                200, json={"status": "ok", "database_ok": True}, request=request
            )
        if request.method == "GET" and path == "/api/documents":
            return httpx.Response(200, json={"documents": []}, request=request)
        if request.method == "POST" and path == "/api/search":
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "query": body["query"],
                    "top_k": body["top_k"],
                    "mode": body["mode"],
                    "results": [],
                },
                request=request,
            )
        if request.method == "GET" and path == "/api/companies":
            return httpx.Response(
                200,
                json={
                    "companies": [
                        {
                            "id": "c1",
                            "company_name": "测试建设",
                            "credit_code": "9133",
                            "bank_name": "秘密银行",
                            "bank_account": "6222",
                            "phone": "13800000000",
                        },
                        {
                            "id": "c2",
                            "company_name": "第二公司",
                            "credit_code": "9134",
                            "bank_name": "",
                            "bank_account": "",
                            "phone": "",
                        },
                    ]
                },
                request=request,
            )
        if request.method == "GET" and path == "/api/bid-runs":
            return httpx.Response(
                200,
                json={"runs": [], "query": dict(request.url.params)},
                request=request,
            )
        if request.method == "GET" and path == "/api/bid-runs/run-1":
            return httpx.Response(
                200,
                json={"run_id": "run-1", "doc_id": "doc-1", "status": "paused"},
                request=request,
            )
        if request.method == "GET" and path == "/api/jobs/job-1":
            return httpx.Response(
                200, json={"job_id": "job-1", "status": "running"}, request=request
            )
        if request.method == "GET" and path == "/api/bid-runs/run-1/figures":
            return httpx.Response(
                200,
                json={
                    "run_id": "run-1",
                    "enabled": True,
                    "figures": [],
                    "stats": {},
                },
                request=request,
            )
        if request.method == "GET" and path == "/api/bids/profile-template":
            return httpx.Response(200, json={"company": {}}, request=request)
        if request.method == "POST" and path == "/api/bid-runs":
            body = json.loads(request.content)
            return httpx.Response(
                201,
                json={
                    "run_id": "run-new",
                    "doc_id": body["doc_id"],
                    "status": "draft",
                    "revision": 1,
                },
                request=request,
            )
        if (
            request.method == "GET"
            and path
            == "/api/documents/doc-1/bids/run-1/artifacts"
        ):
            return httpx.Response(
                200,
                json={
                    "doc_id": "doc-1",
                    "run_id": "run-1",
                    "artifacts": [
                        {"path": "gate.json", "download_url": "/gate.json"},
                        {
                            "path": "profile.json",
                            "download_url": "/profile.json",
                        },
                    ],
                },
                request=request,
            )
        if (
            request.method == "GET"
            and path
            == "/api/documents/doc-1/bids/run-1/artifacts/bid.md"
        ):
            return httpx.Response(
                200,
                text="# 投标文件",
                headers={"content-type": "text/markdown; charset=utf-8"},
                request=request,
            )
        if (
            request.method == "GET"
            and path
            == "/api/documents/doc-1/bids/run-1/artifacts/figures/fig-01.png"
        ):
            return httpx.Response(
                200,
                content=b"\x89PNG",
                headers={"content-type": "image/png"},
                request=request,
            )
        return httpx.Response(
            404,
            json={"detail": f"unexpected {request.method} {path}"},
            request=request,
        )


async def invoke(server: Any, name: str, arguments: dict[str, Any]) -> Any:
    tool = server._tool_manager.get_tool(name)
    assert tool is not None
    return await tool.run(arguments)


def test_tool_registry_and_readonly_mapping() -> None:
    recorder = Recorder()

    async def run() -> None:
        proxy = ApiProxy(
            "http://api.local", transport=httpx.MockTransport(recorder.handler)
        )
        server = create_server(proxy=proxy)
        try:
            names = {
                tool.name for tool in server._tool_manager.list_tools()
            }
            assert names == EXPECTED_TOOLS

            health = await invoke(server, "health", {})
            assert health["status"] == "ok"
            assert (await invoke(server, "list_documents", {}))["documents"] == []

            search = await invoke(
                server,
                "search_tender",
                {"query": "施工组织设计", "top_k": 99},
            )
            assert search["top_k"] == 30
            assert search["mode"] == "auto"
            search_request = next(
                request
                for request in recorder.requests
                if request.url.path == "/api/search"
            )
            search_body = json.loads(search_request.content)
            assert search_body == {
                "query": "施工组织设计",
                "top_k": 30,
                "mode": "auto",
            }

            companies = await invoke(server, "list_companies", {"limit": 1})
            assert companies == [
                {
                    "id": "c1",
                    "company_name": "测试建设",
                    "credit_code": "9133",
                }
            ]

            runs = await invoke(
                server,
                "list_bid_runs",
                {"doc_id": "doc-1", "status": "paused", "limit": 5},
            )
            assert runs["query"] == {
                "limit": "5",
                "doc_id": "doc-1",
                "status": "paused",
            }
            assert (await invoke(server, "get_job", {"job_id": "job-1"}))[
                "status"
            ] == "running"
            figures = await invoke(server, "list_bid_figures", {"run_id": "run-1"})
            assert figures["run_id"] == "run-1"
            template = await invoke(server, "get_bid_profile_template", {})
            assert template == {"company": {}}
        finally:
            await proxy.aclose()

    asyncio.run(run())


def test_create_bid_run_never_sends_inline_profile() -> None:
    recorder = Recorder()

    async def run() -> None:
        proxy = ApiProxy(
            "http://api.local", transport=httpx.MockTransport(recorder.handler)
        )
        server = create_server(proxy=proxy)
        try:
            created = await invoke(
                server,
                "create_bid_run",
                {"doc_id": "doc-1", "company_id": "c1", "max_sections": 2},
            )
            assert created["run_id"] == "run-new"
            body = json.loads(recorder.requests[-1].content)
            assert body == {
                "doc_id": "doc-1",
                "company_id": "c1",
                "max_sections": 2,
            }
            assert "profile" not in body

            await invoke(server, "create_bid_run", {"doc_id": "doc-2"})
            assert json.loads(recorder.requests[-1].content) == {
                "doc_id": "doc-2"
            }
        finally:
            await proxy.aclose()

    asyncio.run(run())


def test_artifacts_are_allowlisted_and_profile_is_never_returned() -> None:
    recorder = Recorder()

    async def run() -> None:
        proxy = ApiProxy(
            "http://api.local", transport=httpx.MockTransport(recorder.handler)
        )
        server = create_server(proxy=proxy)
        try:
            with pytest.raises(ToolError, match="artifact is not available"):
                await invoke(
                    server,
                    "read_artifact_text",
                    {"run_id": "run-1", "path": "profile.json"},
                )
            with pytest.raises(ToolError, match="invalid artifact path"):
                await invoke(
                    server,
                    "read_artifact_text",
                    {"run_id": "run-1", "path": "figures/../profile.json"},
                )
            with pytest.raises(ToolError, match="artifact is not available"):
                await invoke(
                    server,
                    "read_artifact_text",
                    {"run_id": "run-1", "path": "notes.txt"},
                )
            assert recorder.requests == []

            artifacts = await invoke(
                server, "list_artifacts", {"run_id": "run-1"}
            )
            assert [item["path"] for item in artifacts["artifacts"]] == [
                "gate.json"
            ]

            text = await invoke(
                server,
                "read_artifact_text",
                {"run_id": "run-1", "path": "bid.md"},
            )
            assert text["content"] == "# 投标文件"
            assert text["content_type"].startswith("text/markdown")
            assert text["truncated"] is False
            assert (
                recorder.requests[-1].url.path
                == "/api/documents/doc-1/bids/run-1/artifacts/bid.md"
            )

            with pytest.raises(ToolError, match="artifact is not text"):
                await invoke(
                    server,
                    "read_artifact_text",
                    {"run_id": "run-1", "path": "figures/fig-01.png"},
                )
        finally:
            await proxy.aclose()

    asyncio.run(run())


def test_api_guard_errors_and_network_failures_are_reported() -> None:
    async def guarded(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/health":
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(
            409,
            json={
                "detail": "cannot regenerate figures while the run is active"
            },
            request=request,
        )

    async def run() -> None:
        proxy = ApiProxy(
            "http://api.local", transport=httpx.MockTransport(guarded)
        )
        server = create_server(proxy=proxy)
        try:
            with pytest.raises(ToolError, match="cannot regenerate figures"):
                await invoke(
                    server,
                    "regenerate_bid_figures",
                    {"run_id": "run-1"},
                )
            with pytest.raises(ToolError, match="cannot reach Much Agents API"):
                await invoke(server, "health", {})
        finally:
            await proxy.aclose()

    asyncio.run(run())


def test_http_binding_requires_token_for_non_loopback() -> None:
    validate_http_binding("127.0.0.1", "")
    validate_http_binding("localhost", "")
    validate_http_binding("::1", "")
    validate_http_binding("0.0.0.0", "secret")
    with pytest.raises(ValueError, match="MCP_HTTP_TOKEN"):
        validate_http_binding("0.0.0.0", "")

    assert _http_security("127.0.0.1") is None
    security = _http_security("192.168.1.10")
    assert security is not None
    assert "192.168.1.10:*" in security.allowed_hosts


def test_bearer_token_middleware() -> None:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        await Response("ok")(scope, receive, send)

    middleware = BearerTokenMiddleware(app, "secret")

    async def run() -> None:
        transport = httpx.ASGITransport(app=middleware)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://mcp.local"
        ) as client:
            rejected = await client.get("/mcp")
            assert rejected.status_code == 401
            assert "Bearer" in rejected.headers["www-authenticate"]

            wrong = await client.get(
                "/mcp", headers={"Authorization": "Bearer wrong"}
            )
            assert wrong.status_code == 401

            accepted = await client.get(
                "/mcp", headers={"Authorization": "Bearer secret"}
            )
            assert accepted.status_code == 200
            assert accepted.text == "ok"

    asyncio.run(run())


def test_check_cli_lists_tools_and_rejects_public_http_without_token(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("MCP_API_BASE_URL", "http://127.0.0.1:8001")
    monkeypatch.delenv("MCP_HTTP_TOKEN", raising=False)

    main(["--check"])
    output = capsys.readouterr().out
    assert "Much Agents MCP server" in output
    assert f"Tools ({len(EXPECTED_TOOLS)})" in output
    assert "read_artifact_text" in output
    assert "regenerate_bid_figures" in output

    with pytest.raises(SystemExit) as excinfo:
        main(["--transport", "http", "--host", "0.0.0.0", "--check"])
    assert excinfo.value.code == 2
    assert "MCP_HTTP_TOKEN" in capsys.readouterr().err


def test_settings_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_API_BASE_URL", "http://127.0.0.1:8001/")
    monkeypatch.setenv("MCP_HTTP_HOST", "localhost")
    monkeypatch.setenv("MCP_HTTP_PORT", "8765")
    settings = McpServerSettings.from_env()
    assert settings.api_base_url == "http://127.0.0.1:8001"
    assert settings.http_host == "localhost"
    assert settings.http_port == 8765

    monkeypatch.setenv("MCP_API_BASE_URL", "ftp://127.0.0.1")
    with pytest.raises(ValueError, match="http"):
        McpServerSettings.from_env()

    monkeypatch.setenv("MCP_API_BASE_URL", "http://127.0.0.1:8001/api")
    with pytest.raises(ValueError, match="path"):
        McpServerSettings.from_env()
