"""Tender evidence retrieval: Chroma + Zhipu query embedding, keyword fallback."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Protocol

from .config import BidSettings
from .context import ChunkRecord, TenderContext
from .models import Evidence

logger = logging.getLogger(__name__)

_PREFIX_RE = re.compile(r"^\[项目名\][^\n]*\n章节:[^\n]*\n\n", re.DOTALL)
_TOKEN_RE = re.compile(r"[A-Za-z0-9_.-]+|[一-鿿]{2,}")


class TenderRetriever(Protocol):
    def retrieve(self, query: str, doc_id: str, k: int) -> list[Evidence]: ...


def _strip_prefix(text: str) -> str:
    return _PREFIX_RE.sub("", text or "", count=1).strip()


def _truncate(evidence: list[Evidence], max_chars: int) -> list[Evidence]:
    result: list[Evidence] = []
    used = 0
    for item in evidence:
        if used >= max_chars:
            break
        remaining = max_chars - used
        text = item.text[:remaining]
        if not text:
            break
        result.append(
            Evidence(
                chunk_id=item.chunk_id,
                text=text,
                section_path=item.section_path,
                page_start=item.page_start,
                page_end=item.page_end,
                score=item.score,
            )
        )
        used += len(text)
    return result


@dataclass
class ChromaTenderRetriever:
    settings: BidSettings
    collection: object | None = None
    embedder: object | None = None

    def _ensure(self) -> None:
        if self.collection is None:
            from src.parsing.ingest import ChromaStore

            self.collection = ChromaStore(self.settings.base).ensure_collection()
        if self.embedder is None:
            from src.parsing.ingest import ZhipuEmbedder

            self.embedder = ZhipuEmbedder(self.settings.base)

    def retrieve(self, query: str, doc_id: str, k: int) -> list[Evidence]:
        self._ensure()
        vector = self.embedder.embed([query])[0]  # type: ignore[union-attr]
        result = self.collection.query(  # type: ignore[union-attr]
            query_embeddings=[vector],
            n_results=max(1, k),
            where={"doc_id": doc_id},
            include=["documents", "metadatas", "distances"],
        )
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        evidence: list[Evidence] = []
        seen: set[str] = set()
        for text, metadata, distance in zip(documents, metadatas, distances):
            metadata = metadata or {}
            chunk_id = str(metadata.get("chunk_id") or "")
            if chunk_id and chunk_id in seen:
                continue
            seen.add(chunk_id)
            evidence.append(
                Evidence(
                    chunk_id=chunk_id,
                    text=_strip_prefix(str(text or "")),
                    section_path=str(metadata.get("section_path") or ""),
                    page_start=int(metadata.get("page_start") or 0),
                    page_end=int(metadata.get("page_end") or 0),
                    score=float(distance) if distance is not None else None,
                )
            )
        return _truncate(evidence, self.settings.max_evidence_chars)


@dataclass
class KeywordTenderRetriever:
    chunks: list[ChunkRecord]
    max_evidence_chars: int = 6000

    def _score(self, query: str, chunk: ChunkRecord) -> float:
        tokens = {token.lower() for token in _TOKEN_RE.findall(query)}
        if not tokens:
            return 0.0
        text = chunk.text.lower()
        path = chunk.section_path
        score = 0.0
        for token in tokens:
            if token in text:
                score += 2.0
            if token in path.lower():
                score += 1.0
        for boost in ("施工组织设计", "技术条款", "评标办法", "合同条款"):
            if boost in path:
                score += 0.25
        return score

    def retrieve(self, query: str, doc_id: str, k: int) -> list[Evidence]:
        scored = [
            (self._score(query, chunk), chunk)
            for chunk in self.chunks
            if chunk.metadata.get("doc_id") in (None, doc_id)
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        evidence = [
            Evidence(
                chunk_id=chunk.chunk_id,
                text=chunk.text,
                section_path=chunk.section_path,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                score=round(score, 4),
            )
            for score, chunk in scored[: max(1, k)]
            if score > 0
        ]
        return _truncate(evidence, self.max_evidence_chars)


@dataclass
class FallbackTenderRetriever:
    primary: TenderRetriever | None
    fallback: TenderRetriever
    warnings: list[str] = field(default_factory=list)

    def retrieve(self, query: str, doc_id: str, k: int) -> list[Evidence]:
        if self.primary is not None:
            try:
                evidence = self.primary.retrieve(query, doc_id, k)
                if evidence:
                    return evidence
                self.warnings.append("Chroma returned no evidence; used keyword retrieval")
            except Exception as exc:  # noqa: BLE001 - fallback is best effort
                logger.warning("Chroma retrieval failed: %s", exc)
                self.warnings.append("Chroma retrieval failed; used keyword retrieval")
        return self.fallback.retrieve(query, doc_id, k)


def build_retriever(settings: BidSettings, context: TenderContext) -> FallbackTenderRetriever:
    keyword = KeywordTenderRetriever(context.chunks, settings.max_evidence_chars)
    if not settings.use_chroma:
        return FallbackTenderRetriever(None, keyword, ["Chroma disabled; used keyword retrieval"])
    return FallbackTenderRetriever(
        ChromaTenderRetriever(settings),
        keyword,
    )
