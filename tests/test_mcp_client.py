from __future__ import annotations

import asyncio
import base64
import json
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import pytest
from mcp import types
from PIL import Image
from pydantic import ValidationError

from src.bidding.models import Evidence
from src.bidding.figures.schema import FigureSpec
from src.bidding.staged import _merge_evidence
from src.mcp_client.cli import main as cli_main
from src.mcp_client.config import (
    McpClientConfig,
    McpClientConfigError,
    McpServerConfig,
    McpToolConfig,
)
from src.mcp_client.manager import (
    McpClientError,
    McpClientManager,
    render_arguments,
    threadsafe_external_renderer,
)


class FakeSession:
    def __init__(
        self,
        responses: dict[str, Any] | None = None,
        *,
        tools: list[str] | None = None,
        tools_error: BaseException | None = None,
    ) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.tools = tools or []
        self.tools_error = tools_error
        self.closed = False

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        read_timeout_seconds: Any = None,
    ) -> types.CallToolResult:
        del read_timeout_seconds
        self.calls.append((name, arguments or {}))
        response = self.responses.get(name)
        if isinstance(response, BaseException):
            raise response
        if callable(response):
            return response(arguments or {})
        if response is None:
            raise AssertionError(f"unexpected tool call: {name}")
        return response

    async def list_tools(self) -> types.ListToolsResult:
        if self.tools_error is not None:
            raise self.tools_error
        return types.ListToolsResult(
            tools=[
                types.Tool(name=name, inputSchema={"type": "object"})
                for name in self.tools
            ]
        )


def _connector(
    sessions: dict[str, FakeSession],
    failures: set[str] | None = None,
):
    failed = failures or set()

    @asynccontextmanager
    async def connect(name: str, server: McpServerConfig) -> AsyncIterator[FakeSession]:
        del server
        if name in failed:
            raise RuntimeError("connection refused")
        try:
            yield sessions[name]
        finally:
            sessions[name].closed = True

    return connect


def _tool_config(
    name: str = "search",
    purpose: str = "company",
    arguments: dict[str, Any] | None = None,
    *,
    max_chars: int = 4000,
    figure_kinds: list[str] | None = None,
) -> McpToolConfig:
    return McpToolConfig(
        name=name,
        purpose=purpose,
        arguments=arguments or {},
        max_chars=max_chars,
        figure_kinds=figure_kinds or [],
    )


def _stdio_server(name: str, tools: list[McpToolConfig]) -> McpServerConfig:
    del name
    return McpServerConfig(
        transport="stdio",
        command="python",
        args=["-m", "fake_mcp"],
        allowed_tools=tools,
    )


def _config(
    servers: dict[str, McpServerConfig],
    **limits: Any,
) -> McpClientConfig:
    return McpClientConfig(
        enabled=True,
        config_path=Path("mcp_servers.json"),
        servers=servers,
        **limits,
    )


def _text_result(text: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)]
    )


def _png_bytes(size: tuple[int, int] = (40, 30)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, (20, 80, 160)).save(buffer, format="PNG")
    return buffer.getvalue()


def _image_result(png: bytes, mime: str = "image/png") -> types.CallToolResult:
    return types.CallToolResult(
        content=[
            types.ImageContent(
                type="image",
                data=base64.b64encode(png).decode("ascii"),
                mimeType=mime,
            )
        ]
    )


def _spec(**changes: Any) -> dict[str, Any]:
    value = {
        "kind": "site_plan",
        "title": "施工总平面示意图",
        "data": {"zones": [{"name": "办公区", "kind": "office"}]},
        "disclaimer": "布置示意",
        "section_id": "att-06-05",
        "section_title": "附件五 施工总平面图",
        "project_name": "测试供水工程",
        "doc_id": "doc-1",
    }
    value.update(changes)
    return value


def test_config_file_placeholders_and_env_expansion(tmp_path: Path) -> None:
    path = tmp_path / "mcp_servers.json"
    path.write_text(
        json.dumps(
            {
                "servers": {
                    "company_kb": {
                        "transport": "stdio",
                        "command": "python",
                        "args": ["-m", "kb_mcp"],
                        "allowed_tools": [
                            {
                                "name": "search",
                                "purpose": "company",
                                "arguments": {
                                    "query": "${query}",
                                    "limit": 5,
                                },
                                "max_chars": 1200,
                            }
                        ],
                    },
                    "drawing": {
                        "transport": "streamable_http",
                        "url": "http://127.0.0.1:9000/mcp",
                        "headers": {"Authorization": "Bearer ${DRAWING_TOKEN}"},
                        "allowed_tools": [
                            {
                                "name": "render_site_plan",
                                "purpose": "figure",
                                "arguments": {"spec": "${figure_spec}"},
                                "figure_kinds": ["site_plan"],
                            }
                        ],
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    config = McpClientConfig.from_env(
        project_root=tmp_path,
        env={
            "MCP_CLIENT_ENABLED": "true",
            "DRAWING_TOKEN": "secret-token",
        },
        config_path=path,
    )
    assert config.enabled is True
    assert set(config.servers) == {"company_kb", "drawing"}
    assert config.servers["drawing"].headers == {
        "Authorization": "Bearer secret-token"
    }
    assert config.has_tools("company")
    assert config.has_tools("figure")
    assert config.tools_for("figure")[0][0] == "drawing"
    assert config.servers["company_kb"].allowed_tools[0].max_chars == 1200

    with pytest.raises(McpClientConfigError, match="DRAWING_TOKEN"):
        McpClientConfig.from_env(
            project_root=tmp_path,
            env={"MCP_CLIENT_ENABLED": "true"},
            config_path=path,
        )


def test_config_rejects_unknown_placeholders_and_bad_transports(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValidationError, match="placeholders"):
        _tool_config(arguments={"query": "${query}", "profile": "${profile}"})

    with pytest.raises(ValidationError, match="url"):
        McpServerConfig(
            transport="streamable_http",
            allowed_tools=[_tool_config()],
        )
    with pytest.raises(ValidationError, match="command"):
        McpServerConfig(
            transport="stdio",
            allowed_tools=[_tool_config()],
        )
    with pytest.raises(ValidationError, match="figure_kinds"):
        _tool_config(purpose="company", figure_kinds=["site_plan"])

    path = tmp_path / "mcp_servers.json"
    path.write_text(
        json.dumps(
            {
                "servers": {
                    "empty": {
                        "transport": "stdio",
                        "command": "python",
                        "allowed_tools": [],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(McpClientConfigError, match="invalid"):
        McpClientConfig.from_env(
            project_root=tmp_path,
            env={"MCP_CLIENT_ENABLED": "true"},
            config_path=path,
        )

    path.unlink()
    disabled = McpClientConfig.from_env(project_root=tmp_path, env={})
    assert disabled.enabled is False
    assert disabled.servers == {}


def test_render_arguments_are_typed_and_placeholders_are_fixed() -> None:
    values = {
        "query": "施工组织设计",
        "top_k": 7,
        "section_id": "narr-01",
        "figure_spec": {"kind": "process_flow"},
    }
    rendered = render_arguments(
        {
            "query": "${query}",
            "limit": "${top_k}",
            "spec": "${figure_spec}",
            "label": "sec-${section_id}",
            "fixed": 3,
        },
        values,
    )
    assert rendered == {
        "query": "施工组织设计",
        "limit": 7,
        "spec": {"kind": "process_flow"},
        "label": "sec-narr-01",
        "fixed": 3,
    }
    with pytest.raises(McpClientError, match="profile"):
        render_arguments({"x": "${profile}"}, values)


def test_evidence_collection_degrades_and_never_sends_profile() -> None:
    good = FakeSession(
        {
            "search": _text_result("外部企业资料" * 200),
        }
    )
    bad = FakeSession({"kb": asyncio.TimeoutError("slow")})
    config = _config(
        {
            "good": _stdio_server(
                "good",
                [
                    _tool_config(
                        arguments={
                            "query": "${query}",
                            "company": "${company_name}",
                            "project": "${project_name}",
                            "section_id": "${section_id}",
                            "title": "${section_title}",
                            "doc_id": "${doc_id}",
                            "limit": "${top_k}",
                        },
                        max_chars=160,
                    )
                ],
            ),
            "bad": _stdio_server(
                "bad", [_tool_config(name="kb", purpose="evidence")]
            ),
        }
    )

    async def scenario() -> None:
        manager = McpClientManager(
            config,
            connector=_connector({"good": good, "bad": bad}),
        )
        await manager.start()
        try:
            evidence, warnings = await manager.collect_evidence(
                query="施工组织",
                doc_id="doc-1",
                top_k=6,
                company_name="测试建设",
                project_name="测试供水工程",
                section_id="narr-01",
                section_title="施工方案",
            )
        finally:
            await manager.aclose()
        assert good.closed and bad.closed
        assert len(evidence) == 1
        item = evidence[0]
        assert item.chunk_id == "mcp:good:search:1"
        assert item.section_path == "MCP/good/search"
        assert "未经核实" in item.text
        assert len(item.text) <= 160
        assert any("TimeoutError" in warning for warning in warnings)
        assert good.calls
        arguments = good.calls[0][1]
        assert arguments == {
            "query": "施工组织",
            "company": "测试建设",
            "project": "测试供水工程",
            "section_id": "narr-01",
            "title": "施工方案",
            "doc_id": "doc-1",
            "limit": 6,
        }
        serialized = json.dumps(arguments, ensure_ascii=False)
        assert "profile" not in serialized
        assert "bank_account" not in serialized
        assert "credit_code" not in serialized

    asyncio.run(scenario())


def test_manager_connection_failure_is_isolated_and_tools_are_listed() -> None:
    good = FakeSession({}, tools=["search"])

    async def scenario() -> None:
        config = _config(
            {
                "good": _stdio_server(
                    "good", [_tool_config(name="search")]
                ),
                "bad": _stdio_server(
                    "bad", [_tool_config(name="search")]
                ),
            }
        )
        manager = McpClientManager(
            config,
            connector=_connector({"good": good}, failures={"bad"}),
        )
        await manager.start()
        try:
            assert manager.connected_servers == ["good"]
            assert any("bad" in warning for warning in manager.warnings)
            listing = await manager.list_server_tools()
            assert listing == {"good": ["search"]}
            evidence, warnings = await manager.collect_evidence(query="x")
            assert evidence == []
            assert any("good/search" in warning for warning in warnings)
        finally:
            await manager.aclose()
        assert good.closed
        with pytest.raises(McpClientError, match="unavailable"):
            await manager.call_configured_tool("good", "search", {})

    asyncio.run(scenario())


def test_external_drawing_is_validated_saved_and_bridged_from_a_thread(
    tmp_path: Path,
) -> None:
    png = _png_bytes()
    drawing = FakeSession({"render_site_plan": _image_result(png)})
    config = _config(
        {
            "drawing": _stdio_server(
                "drawing",
                [
                    _tool_config(
                        name="render_site_plan",
                        purpose="figure",
                        arguments={"spec": "${figure_spec}"},
                        figure_kinds=["site_plan"],
                    )
                ],
            )
        }
    )
    target = tmp_path / "run" / "figures" / "site.png"
    target.parent.mkdir(parents=True, exist_ok=True)

    async def scenario() -> None:
        manager = McpClientManager(
            config,
            connector=_connector({"drawing": drawing}),
        )
        await manager.start()
        try:
            renderer = threadsafe_external_renderer(manager)
            source = await asyncio.to_thread(renderer, _spec(), target)
        finally:
            await manager.aclose()
        assert source == "mcp:drawing/render_site_plan"
        assert drawing.calls
        sent = drawing.calls[0][1]["spec"]
        assert sent["kind"] == "site_plan"
        assert sent["data"]["zones"][0]["name"] == "办公区"
        assert "profile" not in json.dumps(sent, ensure_ascii=False)
        with Image.open(target) as image:
            assert image.format == "PNG"
            assert image.size == (40, 30)

        with pytest.raises(McpClientError, match="not allowed"):
            await manager.render_external_figure(_spec(kind="org_chart"), target)
        assert len(drawing.calls) == 1

    asyncio.run(scenario())


def test_external_drawing_rejects_bad_mime_and_dimensions(tmp_path: Path) -> None:
    bad_mime = FakeSession(
        {"render": _image_result(_png_bytes(), mime="text/plain")}
    )
    too_large = FakeSession(
        {"render": _image_result(_png_bytes((300, 320)))}
    )

    def config_for(session_name: str) -> McpClientConfig:
        return _config(
            {
                "drawing": _stdio_server(
                    "drawing",
                    [
                        _tool_config(
                            name="render",
                            purpose="figure",
                            arguments={"spec": "${figure_spec}"},
                            figure_kinds=["process_flow"],
                        )
                    ],
                )
            },
            max_image_dimension=256,
        )

    async def scenario() -> None:
        for session in (bad_mime, too_large):
            manager = McpClientManager(
                config_for(session is bad_mime),
                connector=_connector({"drawing": session}),
            )
            await manager.start()
            target = tmp_path / f"{id(session)}.png"
            try:
                with pytest.raises(McpClientError):
                    await manager.render_external_figure(
                        _spec(kind="process_flow"), target
                    )
                assert not target.exists()
            finally:
                await manager.aclose()

    asyncio.run(scenario())


def test_external_drawing_can_download_an_image_url(tmp_path: Path) -> None:
    png = _png_bytes()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            content=png,
            headers={"content-type": "image/png"},
        )

    drawing = FakeSession(
        {"render": _text_result("done: https://files.local/site.png")}
    )
    config = _config(
        {
            "drawing": _stdio_server(
                "drawing",
                [
                    _tool_config(
                        name="render",
                        purpose="figure",
                        arguments={"spec": "${figure_spec}"},
                    )
                ],
            )
        }
    )

    async def scenario() -> None:
        manager = McpClientManager(
            config,
            connector=_connector({"drawing": drawing}),
            http_transport=httpx.MockTransport(handler),
        )
        await manager.start()
        target = tmp_path / "downloaded.png"
        try:
            source = await manager.render_external_figure(
                _spec(kind="site_plan"), target
            )
        finally:
            await manager.aclose()
        assert source == "mcp:drawing/render"
        assert requests and requests[0].url == "https://files.local/site.png"
        with Image.open(target) as image:
            assert image.format == "PNG"

    asyncio.run(scenario())


def test_external_image_url_rejects_non_image_content(tmp_path: Path) -> None:
    drawing = FakeSession(
        {"render": _text_result("https://files.local/not-an-image")}
    )
    config = _config(
        {
            "drawing": _stdio_server(
                "drawing",
                [
                    _tool_config(
                        name="render",
                        purpose="figure",
                        arguments={"spec": "${figure_spec}"},
                    )
                ],
            )
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            content=b"<html>nope</html>",
            headers={"content-type": "text/html"},
        )

    async def scenario() -> None:
        manager = McpClientManager(
            config,
            connector=_connector({"drawing": drawing}),
            http_transport=httpx.MockTransport(handler),
        )
        await manager.start()
        try:
            with pytest.raises(McpClientError, match="content type"):
                await manager.render_external_figure(
                    _spec(kind="site_plan"), tmp_path / "bad.png"
                )
        finally:
            await manager.aclose()
        assert not (tmp_path / "bad.png").exists()

    asyncio.run(scenario())


def test_merge_evidence_reserves_budget_and_prefers_local_tender_evidence() -> None:
    local = [
        Evidence(chunk_id=f"local-{index}", text="L" * 100)
        for index in range(3)
    ]
    external = [
        Evidence(chunk_id=f"mcp:server:tool:{index}", text="E" * 100)
        for index in range(2)
    ]
    merged = _merge_evidence(local, external, 300)
    assert [item.chunk_id for item in merged] == [
        "local-0",
        "local-1",
        "mcp:server:tool:0",
    ]
    assert sum(len(item.text) for item in merged) == 300

    small_external = [Evidence(chunk_id="mcp:s:t:1", text="E" * 50)]
    merged = _merge_evidence(local, small_external, 300)
    assert sum(len(item.text) for item in merged) == 300
    assert merged[-1].chunk_id == "mcp:s:t:1"
    assert sum(len(item.text) for item in merged[:-1]) == 250

    assert _merge_evidence(local, [], 250) == [
        Evidence(chunk_id="local-0", text="L" * 100),
        Evidence(chunk_id="local-1", text="L" * 100),
        Evidence(chunk_id="local-2", text="L" * 50),
    ]


def test_figure_source_accepts_exact_mcp_tool_but_rejects_paths() -> None:
    common = dict(
        figure_id="fig-01-site_plan",
        section_id="att-06-05",
        section_title="施工总平面图",
        kind="site_plan",
        title="施工总平面示意图",
        caption="图 1 施工总平面示意图",
        disclaimer="示意图",
        file="figures/fig-01-site_plan.png",
        source_hash="abc",
    )
    spec = FigureSpec(source="mcp:drawing/render_site_plan", **common)
    assert spec.source == "mcp:drawing/render_site_plan"
    for source in ("mcp:../profile", "mcp:server", "drawing/tool"):
        with pytest.raises(ValidationError, match="figure source"):
            FigureSpec(source=source, **common)


def test_cli_list_prints_configured_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "mcp_servers.json"
    path.write_text(
        json.dumps(
            {
                "servers": {
                    "company_kb": {
                        "transport": "stdio",
                        "command": "python",
                        "allowed_tools": [
                            {
                                "name": "search",
                                "purpose": "company",
                                "arguments": {"query": "${query}"},
                            }
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MCP_CLIENT_ENABLED", "true")
    monkeypatch.setenv("MCP_CLIENT_CONFIG", str(path))
    assert cli_main(["list"]) == 0
    output = capsys.readouterr().out
    assert "company_kb" in output
    assert "search" in output
    assert "purpose=company" in output

    monkeypatch.delenv("MCP_CLIENT_CONFIG")
    monkeypatch.setenv("MCP_CLIENT_CONFIG", str(tmp_path / "missing.json"))
    monkeypatch.setenv("MCP_CLIENT_ENABLED", "false")
    assert cli_main(["list"]) == 0
