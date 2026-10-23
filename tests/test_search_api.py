from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.jobs import JobStore
from api.schemas import SearchRequest
from api.search import SearchError, SearchService
from src.bidding.models import Evidence
from tests.fakes import (
    FakeDispatcher,
    FakeSemanticRetriever,
    make_api_settings,
    make_bid_settings,
    make_parse_settings,
    write_parsed_fixture,
    write_search_doc,
)


def make_service(
    tmp_path: Path,
    *,
    semantic: FakeSemanticRetriever | None = None,
) -> SearchService:
    return SearchService(
        parse_settings=make_parse_settings(tmp_path),
        bid_settings=make_bid_settings(tmp_path, BID_USE_CHROMA="false"),
        semantic_retriever=semantic,
    )


def make_app(tmp_path: Path, service: SearchService):
    return create_app(
        api_settings=make_api_settings(tmp_path, TASK_BACKEND="local"),
        parse_settings=make_parse_settings(tmp_path),
        bid_settings=make_bid_settings(tmp_path),
        store=JobStore(),
        dispatcher=FakeDispatcher(),
        bid_dispatcher=FakeDispatcher(),
        search_service=service,
    )


def test_keyword_single_doc(tmp_path: Path) -> None:
    write_parsed_fixture(tmp_path, "doc-1")
    service = make_service(tmp_path)
    response = service.search(
        SearchRequest(
            query="施工组织设计",
            doc_id="doc-1",
            top_k=5,
            mode="keyword",
        )
    )
    assert response.mode_used == "keyword"
    assert response.results
    hit = response.results[0]
    assert hit.doc_id == "doc-1"
    assert hit.project_name == "测试供水工程"
    assert hit.section_path
    assert hit.page_start >= 1
    assert 0 <= (hit.score or 0) <= 1
    assert hit.snippet
    assert "施工组织设计" in hit.highlights
    assert response.elapsed_ms >= 0


def test_keyword_cross_document(tmp_path: Path) -> None:
    write_search_doc(
        tmp_path,
        "doc-a",
        [{"text": "施工组织设计质量安全措施"}],
        project_name="项目 A",
    )
    write_search_doc(
        tmp_path,
        "doc-b",
        [{"text": "施工组织设计进度计划"}],
        project_name="项目 B",
    )
    response = make_service(tmp_path).search(
        SearchRequest(query="施工组织设计", mode="keyword", top_k=10)
    )
    assert {hit.doc_id for hit in response.results} == {"doc-a", "doc-b"}
    assert {hit.project_name for hit in response.results} == {"项目 A", "项目 B"}


def test_auto_falls_back_when_chroma_disabled(tmp_path: Path) -> None:
    write_parsed_fixture(tmp_path, "doc-1")
    response = make_service(tmp_path).search(
        SearchRequest(query="施工组织设计", doc_id="doc-1", mode="auto")
    )
    assert response.mode_used == "keyword"
    assert response.results
    assert any("回退关键词" in warning for warning in response.warnings)


def test_semantic_success_and_score_normalization(tmp_path: Path) -> None:
    write_search_doc(
        tmp_path,
        "doc-a",
        [{"text": "质量安全进度措施"}],
        project_name="项目 A",
    )
    semantic = FakeSemanticRetriever(
        [
            Evidence(
                chunk_id="doc-a:c1",
                text="章节: 测试\n\n质量安全进度措施",
                section_path="测试章节",
                page_start=2,
                page_end=3,
                score=0.2,
                doc_id="doc-a",
            )
        ]
    )
    response = make_service(tmp_path, semantic=semantic).search(
        SearchRequest(query="质量安全", doc_id="doc-a", mode="semantic")
    )
    assert response.mode_used == "semantic"
    assert response.results[0].score == pytest.approx(0.8)
    assert response.results[0].text == "质量安全进度措施"
    assert semantic.calls == [("质量安全", "doc-a", 8)]


def test_semantic_unavailable_returns_503(tmp_path: Path) -> None:
    write_parsed_fixture(tmp_path, "doc-1")
    service = make_service(tmp_path)
    with pytest.raises(SearchError) as exc:
        service.search(SearchRequest(query="质量", mode="semantic"))
    assert exc.value.status_code == 503


def test_semantic_exception_returns_503(tmp_path: Path) -> None:
    write_parsed_fixture(tmp_path, "doc-1")
    semantic = FakeSemanticRetriever(error=RuntimeError("chroma down"))
    service = make_service(tmp_path, semantic=semantic)
    with pytest.raises(SearchError) as exc:
        service.search(SearchRequest(query="质量", mode="semantic"))
    assert exc.value.status_code == 503


def test_auto_semantic_exception_falls_back(tmp_path: Path) -> None:
    write_parsed_fixture(tmp_path, "doc-1")
    semantic = FakeSemanticRetriever(error=RuntimeError("chroma down"))
    response = make_service(tmp_path, semantic=semantic).search(
        SearchRequest(query="施工组织设计", doc_id="doc-1", mode="auto")
    )
    assert response.mode_used == "keyword"
    assert response.results
    assert any("回退关键词" in warning for warning in response.warnings)


def test_unknown_and_unsafe_doc(tmp_path: Path) -> None:
    write_parsed_fixture(tmp_path, "doc-1")
    service = make_service(tmp_path)
    with pytest.raises(SearchError) as missing:
        service.search(SearchRequest(query="质量", doc_id="missing", mode="keyword"))
    assert missing.value.status_code == 404
    with pytest.raises(SearchError) as unsafe:
        service.search(SearchRequest(query="质量", doc_id="../escape", mode="keyword"))
    assert unsafe.value.status_code == 422


def test_bad_lines_skipped_and_partial_doc_is_searchable(tmp_path: Path) -> None:
    doc_dir = write_search_doc(
        tmp_path,
        "partial-doc",
        [{"text": "施工组织设计质量措施"}],
        catalog=False,
    )
    with (doc_dir / "chunks.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{bad json\n")
    response = make_service(tmp_path).search(
        SearchRequest(query="施工组织设计", doc_id="partial-doc", mode="keyword")
    )
    assert response.results
    assert response.results[0].project_name is None
    assert any("异常分块" in warning for warning in response.warnings)


def test_search_strips_table_markup(tmp_path: Path) -> None:
    write_search_doc(
        tmp_path,
        "doc-html",
        [
            {
                "text": "<table><tr><td>施工组织设计</td><td>30 分</td></tr></table>"
            }
        ],
    )
    response = make_service(tmp_path).search(
        SearchRequest(query="施工组织设计", doc_id="doc-html", mode="keyword")
    )
    hit = response.results[0]
    assert "<table>" not in hit.text
    assert "施工组织设计" in hit.text
    assert "30 分" in hit.text


def test_top_k_is_not_silently_dropped(tmp_path: Path) -> None:
    chunks = [{"text": f"施工组织设计质量措施 {index}"} for index in range(8)]
    write_search_doc(tmp_path, "doc-k", chunks)
    response = make_service(tmp_path).search(
        SearchRequest(query="施工组织设计", doc_id="doc-k", mode="keyword", top_k=5)
    )
    assert len(response.results) == 5


def test_api_search_and_validation(tmp_path: Path) -> None:
    write_parsed_fixture(tmp_path, "doc-1")
    service = make_service(tmp_path)
    app = make_app(tmp_path, service)
    with TestClient(app) as client:
        response = client.post(
            "/api/search",
            json={"query": "施工组织设计", "doc_id": "doc-1", "mode": "keyword"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["mode_used"] == "keyword"
        assert body["results"][0]["doc_id"] == "doc-1"
        assert "text" in body["results"][0]
        assert "snippet" in body["results"][0]

        assert client.post("/api/search", json={"query": "   "}).status_code == 422
        assert (
            client.post(
                "/api/search",
                json={"query": "质量", "top_k": 0},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/search",
                json={"query": "质量", "doc_id": "missing"},
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/api/search",
                json={"query": "质量", "mode": "semantic"},
            ).status_code
            == 503
        )


def test_app_default_search_service(tmp_path: Path) -> None:
    app = create_app(
        api_settings=make_api_settings(tmp_path, TASK_BACKEND="local"),
        parse_settings=make_parse_settings(tmp_path),
        bid_settings=make_bid_settings(tmp_path),
        store=JobStore(),
        dispatcher=FakeDispatcher(),
        bid_dispatcher=FakeDispatcher(),
    )
    assert isinstance(app.state.search_service, SearchService)
