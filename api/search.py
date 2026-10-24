"""Tender passage search service: semantic first, keyword fallback."""

from __future__ import annotations

import html
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from src.bidding.config import BidSettings
from src.bidding.context import ChunkRecord
from src.bidding.models import Evidence
from src.bidding.retrieval import (
    CandidateRetriever,
    ChromaTenderRetriever,
    KeywordTenderRetriever,
    strip_embed_prefix,
)
from src.parsing.config import Settings
from src.storage.base import Storage
from src.storage.errors import StorageObjectNotFound
from src.storage.keys import parsed_prefix

from .artifacts import is_safe_id
from .documents import list_documents
from .schemas import DocumentSummary, SearchHit, SearchRequest, SearchResponse

logger = logging.getLogger(__name__)

_SNIPPET_CHARS = 240
_MAX_SEMANTIC_CANDIDATES = 100
_TERM_SPLIT_RE = re.compile(r"[\s,，。；;、:：/\\|]+")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_TAG_NAMES = (
    r"td|tr|th|table|tbody|thead|tfoot|d|p|a|li|ul|ol|span|div|img|br"
)
_BROKEN_TAG_RE = re.compile(
    rf"<?/?\b(?:{_TAG_NAMES})\b[^<>\n]{{0,100}}>",
    re.IGNORECASE,
)
_OPEN_TAG_FRAGMENT_RE = re.compile(
    rf"</?(?:{_TAG_NAMES})\b",
    re.IGNORECASE,
)
_FOOTER_HASH_RE = re.compile(r"\b[0-9a-fA-F]{24,}-\d{6,}\b")
_WATERMARK_NUMBER_RE = re.compile(r"\b20\d{14,}\b")
_STRAY_ANGLE_RE = re.compile(r"(?<![A-Za-z0-9])[<>]+(?![A-Za-z0-9])")


def _clean_text(text: str) -> str:
    value = html.unescape(text or "")
    value = _HTML_TAG_RE.sub(" ", value)
    value = _BROKEN_TAG_RE.sub(" ", value)
    value = _OPEN_TAG_FRAGMENT_RE.sub(" ", value)
    value = _FOOTER_HASH_RE.sub(" ", value)
    value = _WATERMARK_NUMBER_RE.sub(" ", value)
    value = _STRAY_ANGLE_RE.sub(" ", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


class SearchError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _query_terms(query: str) -> list[str]:
    raw = [query.strip(), *_TERM_SPLIT_RE.split(query.strip())]
    terms: list[str] = []
    for item in raw:
        term = item.strip()
        if len(term) >= 2 and term not in terms:
            terms.append(term)
    if not terms:
        terms.append(query.strip())
    return [term for term in terms if term]


def _make_snippet(text: str, query: str, limit: int = _SNIPPET_CHARS) -> str:
    if len(text) <= limit:
        return text
    terms = _query_terms(query)
    lower = text.lower()
    positions = [lower.find(term.lower()) for term in terms]
    positions = [position for position in positions if position >= 0]
    position = min(positions) if positions else 0
    start = max(0, position - limit // 3)
    end = min(len(text), start + limit)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = f"…{snippet}"
    if end < len(text):
        snippet = f"{snippet}…"
    return snippet


def _highlights(text: str, query: str) -> list[str]:
    lower = text.lower()
    terms = [term for term in _query_terms(query) if term.lower() in lower]
    terms.sort(key=len, reverse=True)
    return terms


def _semantic_score(distance: float | None) -> float | None:
    if distance is None:
        return None
    return round(max(0.0, min(1.0, 1.0 - distance)), 4)


def _load_chunk_lines(
    lines: Iterable[str],
    doc_id: str,
) -> tuple[list[ChunkRecord], int]:
    chunks: list[ChunkRecord] = []
    skipped = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            skipped += 1
            continue
        if not isinstance(row, dict):
            skipped += 1
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            skipped += 1
            continue
        metadata = row.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        metadata = dict(metadata)
        metadata_doc = str(metadata.get("doc_id") or "")
        if metadata_doc and metadata_doc != doc_id:
            skipped += 1
            continue
        metadata["doc_id"] = metadata_doc or doc_id
        chunk_id = str(row.get("id") or metadata.get("chunk_id") or "")
        if not chunk_id:
            skipped += 1
            continue
        try:
            page_start = int(metadata.get("page_start") or 0)
            page_end = int(metadata.get("page_end") or 0)
        except (TypeError, ValueError):
            page_start = page_end = 0
        metadata["page_start"] = page_start
        metadata["page_end"] = page_end
        chunks.append(
            ChunkRecord(
                chunk_id=chunk_id,
                text=text,
                embed_text=str(row.get("embed_text") or text),
                metadata=metadata,
            )
        )
    return chunks, skipped


def _load_chunks(
    path: Path,
    doc_id: str,
) -> tuple[list[ChunkRecord], int]:
    if not path.is_file():
        return [], 0
    try:
        handle = path.open("r", encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        logger.warning("could not open chunks file %s: %s", path, exc)
        return [], 1
    with handle:
        return _load_chunk_lines(handle, doc_id)


class SearchService:
    def __init__(
        self,
        *,
        parse_settings: Settings,
        bid_settings: BidSettings,
        semantic_retriever: CandidateRetriever | None = None,
        catalog_func: Callable[..., list[DocumentSummary]] = list_documents,
        storage: Storage | None = None,
    ) -> None:
        self.parse_settings = parse_settings
        self.bid_settings = bid_settings
        self._semantic_retriever = semantic_retriever
        self._catalog_func = catalog_func
        self._storage = storage

    @property
    def _remote_storage(self) -> Storage | None:
        if self._storage is None or self._storage.backend == "local":
            return None
        return self._storage

    def _semantic_reason(self) -> str | None:
        if self._semantic_retriever is not None:
            return None
        if not self.bid_settings.use_chroma:
            return "语义检索未启用"
        if not self.bid_settings.base.zhipu_api_key:
            return "未配置 ZHIPU_API_KEY"
        return None

    def _semantic(self) -> CandidateRetriever:
        if self._semantic_retriever is None:
            self._semantic_retriever = ChromaTenderRetriever(self.bid_settings)
        return self._semantic_retriever

    def _catalog_names(
        self, allowed_doc_ids: set[str] | None = None
    ) -> tuple[dict[str, str], str | None]:
        storage = self._remote_storage
        kwargs: dict[str, Any] = {
            "limit": 1000,
            "allowed_doc_ids": allowed_doc_ids,
        }
        if storage is not None:
            kwargs["storage"] = storage
        try:
            documents = self._catalog_func(
                self.parse_settings,
                self.bid_settings,
                **kwargs,
            )
        except Exception as exc:  # noqa: BLE001 - search still works without labels
            logger.warning("could not load document catalog: %s", exc)
            return {}, "文档目录读取失败，结果仅显示 doc_id"
        return {
            document.doc_id: document.project_name
            for document in documents
            if document.project_name
        }, None

    def _searchable_dirs(
        self, allowed_doc_ids: set[str] | None = None
    ) -> list[Path]:
        base = self.parse_settings.parsed_dir
        if not base.is_dir():
            return []
        try:
            children = sorted(base.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            logger.warning("could not scan parsed documents: %s", exc)
            return []
        return [
            child
            for child in children
            if child.is_dir()
            and is_safe_id(child.name)
            and (
                allowed_doc_ids is None or child.name in allowed_doc_ids
            )
            and (child / "chunks.jsonl").is_file()
            and (child / "chunks.jsonl").stat().st_size > 0
        ]

    def _safe_doc_dir(self, doc_id: str) -> Path:
        if not is_safe_id(doc_id):
            raise SearchError(422, "invalid doc_id")
        base = self.parse_settings.parsed_dir.resolve()
        candidate = (self.parse_settings.parsed_dir / doc_id).resolve()
        if not candidate.is_relative_to(base):
            raise SearchError(422, "invalid doc_id")
        if not candidate.is_dir() or not (candidate / "chunks.jsonl").is_file():
            raise SearchError(404, "document is unavailable")
        return candidate

    def _remote_searchable_docs(
        self, allowed_doc_ids: set[str] | None = None
    ) -> list[str]:
        storage = self._remote_storage
        if storage is None:
            return []
        docs: set[str] = set()
        for info in storage.list("parsed"):
            parts = info.key.split("/")
            if len(parts) != 3 or parts[0] != "parsed":
                continue
            doc_id, name = parts[1], parts[2]
            if name != "chunks.jsonl" or info.size <= 0 or not is_safe_id(doc_id):
                continue
            if allowed_doc_ids is not None and doc_id not in allowed_doc_ids:
                continue
            docs.add(doc_id)
        return sorted(docs)

    def _remote_doc(self, doc_id: str) -> str:
        storage = self._remote_storage
        if storage is None:
            raise SearchError(404, "document is unavailable")
        if not is_safe_id(doc_id):
            raise SearchError(422, "invalid doc_id")
        key = f"{parsed_prefix(doc_id)}/chunks.jsonl"
        info = storage.stat(key)
        if info is None or info.size <= 0:
            raise SearchError(404, "document is unavailable")
        return doc_id

    def _load_remote_chunks(
        self,
        doc_id: str | None,
        allowed_doc_ids: set[str] | None = None,
    ) -> tuple[list[ChunkRecord], set[str], list[str]]:
        storage = self._remote_storage
        assert storage is not None
        warnings: list[str] = []
        chunks: list[ChunkRecord] = []
        searchable: set[str] = set()
        if (
            doc_id
            and allowed_doc_ids is not None
            and doc_id not in allowed_doc_ids
        ):
            raise SearchError(404, "document is unavailable")
        doc_ids = (
            [self._remote_doc(doc_id)]
            if doc_id
            else self._remote_searchable_docs(allowed_doc_ids)
        )
        if not doc_ids:
            return [], set(), ["没有可检索的已解析文档"]
        skipped = 0
        for current in doc_ids:
            key = f"{parsed_prefix(current)}/chunks.jsonl"
            try:
                doc_chunks, doc_skipped = _load_chunk_lines(
                    storage.iter_lines(key),
                    current,
                )
            except StorageObjectNotFound:
                doc_chunks, doc_skipped = [], 1
            skipped += doc_skipped
            if doc_chunks:
                searchable.add(current)
                chunks.extend(doc_chunks)
        if skipped:
            warnings.append(f"已跳过 {skipped} 行异常分块")
        if doc_id and not chunks:
            raise SearchError(404, "document has no searchable chunks")
        if not chunks:
            warnings.append("没有可检索的分块")
        return chunks, searchable, warnings

    def _load_keyword_chunks(
        self,
        doc_id: str | None,
        allowed_doc_ids: set[str] | None = None,
    ) -> tuple[list[ChunkRecord], set[str], list[str]]:
        if self._remote_storage is not None:
            return self._load_remote_chunks(doc_id, allowed_doc_ids)
        warnings: list[str] = []
        chunks: list[ChunkRecord] = []
        searchable: set[str] = set()
        if (
            doc_id
            and allowed_doc_ids is not None
            and doc_id not in allowed_doc_ids
        ):
            raise SearchError(404, "document is unavailable")
        directories = (
            [self._safe_doc_dir(doc_id)]
            if doc_id
            else self._searchable_dirs(allowed_doc_ids)
        )
        if not directories:
            return [], set(), ["没有可检索的已解析文档"]
        skipped = 0
        for directory in directories:
            doc_chunks, doc_skipped = _load_chunks(
                directory / "chunks.jsonl",
                directory.name,
            )
            skipped += doc_skipped
            if doc_chunks:
                searchable.add(directory.name)
                chunks.extend(doc_chunks)
        if skipped:
            warnings.append(f"已跳过 {skipped} 行异常分块")
        if doc_id and not chunks:
            raise SearchError(404, "document has no searchable chunks")
        if not chunks:
            warnings.append("没有可检索的分块")
        return chunks, searchable, warnings

    def _to_hits(
        self,
        evidence: list[Evidence],
        *,
        mode_used: str,
        names: dict[str, str],
        query: str,
        top_k: int,
    ) -> list[SearchHit]:
        max_raw = max((item.score or 0.0) for item in evidence) if evidence else 0.0
        hits: list[SearchHit] = []
        seen: set[tuple[str, str]] = set()
        for item in evidence:
            key = (item.doc_id, item.chunk_id)
            if key in seen:
                continue
            seen.add(key)
            text = _clean_text(strip_embed_prefix(item.text))
            if not text:
                continue
            if mode_used == "semantic":
                score = _semantic_score(item.score)
            else:
                raw = item.score or 0.0
                score = round(raw / max_raw, 4) if max_raw > 0 else None
            hits.append(
                SearchHit(
                    doc_id=item.doc_id,
                    project_name=names.get(item.doc_id),
                    chunk_id=item.chunk_id,
                    section_path=item.section_path,
                    page_start=item.page_start,
                    page_end=item.page_end,
                    text=text,
                    snippet=_make_snippet(text, query),
                    highlights=_highlights(text, query),
                    score=score,
                    match_type=mode_used,  # type: ignore[arg-type]
                )
            )
            if len(hits) >= top_k:
                break
        return hits

    def search(
        self,
        request: SearchRequest,
        *,
        allowed_doc_ids: set[str] | None = None,
    ) -> SearchResponse:
        started = time.perf_counter()
        query = request.query.strip()
        if not query:
            raise SearchError(422, "query must not be blank")
        doc_id = (request.doc_id or "").strip() or None

        if allowed_doc_ids is not None:
            if doc_id is not None:
                if doc_id not in allowed_doc_ids:
                    raise SearchError(404, "document is unavailable")
            elif not allowed_doc_ids:
                return SearchResponse(
                    query=query,
                    doc_id=None,
                    mode=request.mode,
                    mode_used="keyword",
                    results=[],
                    warnings=["没有可检索的已解析文档"],
                    elapsed_ms=round(
                        (time.perf_counter() - started) * 1000, 1
                    ),
                )

        if doc_id:
            if self._remote_storage is not None:
                self._remote_doc(doc_id)
            else:
                self._safe_doc_dir(doc_id)
        chunks, searchable, warnings = self._load_keyword_chunks(
            doc_id, allowed_doc_ids
        )
        names, catalog_warning = self._catalog_names(allowed_doc_ids)
        if catalog_warning:
            warnings.append(catalog_warning)

        mode_used = "keyword"
        evidence: list[Evidence] = []
        if request.mode in ("auto", "semantic"):
            reason = self._semantic_reason()
            if reason is not None:
                if request.mode == "semantic":
                    raise SearchError(503, reason)
                warnings.append("语义检索不可用，已回退关键词检索")
            else:
                try:
                    fetch_k = (
                        request.top_k
                        if doc_id
                        else min(
                            max(request.top_k * 2, 10),
                            _MAX_SEMANTIC_CANDIDATES,
                        )
                    )
                    candidates = self._semantic().fetch(query, doc_id, fetch_k)
                    mode_used = "semantic"
                    if doc_id is None:
                        filtered = [
                            item for item in candidates if item.doc_id in searchable
                        ]
                        dropped = len(candidates) - len(filtered)
                        if dropped:
                            warnings.append(
                                f"已过滤 {dropped} 条来自不可检索文档的语义结果"
                            )
                        candidates = filtered
                    if candidates:
                        evidence = candidates
                    elif request.mode == "semantic":
                        warnings.append("语义检索未返回结果")
                    else:
                        warnings.append("语义检索未返回结果，已回退关键词检索")
                except Exception as exc:  # noqa: BLE001 - auto must degrade
                    logger.warning("semantic search failed: %s", exc)
                    if request.mode == "semantic":
                        raise SearchError(503, "语义检索不可用") from exc
                    warnings.append("语义检索不可用，已回退关键词检索")
                    mode_used = "keyword"

        if request.mode == "keyword" or (request.mode == "auto" and not evidence):
            keyword = KeywordTenderRetriever(chunks, max_evidence_chars=10**9)
            evidence = keyword.rank(query, doc_id, request.top_k)
            mode_used = "keyword"

        results = self._to_hits(
            evidence,
            mode_used=mode_used,
            names=names,
            query=query,
            top_k=request.top_k,
        )
        warnings = list(dict.fromkeys(warnings))
        return SearchResponse(
            query=query,
            doc_id=doc_id,
            mode=request.mode,
            mode_used=mode_used,  # type: ignore[arg-type]
            results=results,
            warnings=warnings,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
        )


__all__ = ["SearchError", "SearchService"]
