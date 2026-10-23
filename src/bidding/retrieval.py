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

_PREFIX_RE = re.compile(r"^(?:\[项目名\][^\n]*\n)?章节:[^\n]*\n\n", re.DOTALL)
_TOKEN_RE = re.compile(r"[A-Za-z0-9_.-]+|[一-鿿]{2,}")


class TenderRetriever(Protocol):
    def retrieve(
        self,
        query: str,
        doc_id: str | None,
        k: int,
    ) -> list[Evidence]: ...


class CandidateRetriever(Protocol):
    """Non-truncating retrieval used by user-facing search."""

    def fetch(
        self,
        query: str,
        doc_id: str | None,
        k: int,
    ) -> list[Evidence]: ...


def strip_embed_prefix(text: str) -> str:
    return _PREFIX_RE.sub("", text or "", count=1).strip()


def truncate_evidence(
    evidence: list[Evidence],
    max_chars: int,
) -> list[Evidence]:
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
                doc_id=item.doc_id,
            )
        )
        used += len(text)
    return result


# Backwards-compatible private aliases used by older callers/tests.
_strip_prefix = strip_embed_prefix
_truncate = truncate_evidence


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

    def fetch(
        self,
        query: str,
        doc_id: str | None,
        k: int,
    ) -> list[Evidence]:
        """Return raw Chroma candidates without a global character budget."""
        self._ensure()
        vector = self.embedder.embed([query])[0]  # type: ignore[union-attr]
        kwargs: dict[str, object] = {
            "query_embeddings": [vector],
            "n_results": max(1, k),
            "include": ["documents", "metadatas", "distances"],
        }
        if doc_id:
            kwargs["where"] = {"doc_id": doc_id}
        result = self.collection.query(**kwargs)  # type: ignore[union-attr]
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
                    text=strip_embed_prefix(str(text or "")),
                    section_path=str(metadata.get("section_path") or ""),
                    page_start=int(metadata.get("page_start") or 0),
                    page_end=int(metadata.get("page_end") or 0),
                    score=float(distance) if distance is not None else None,
                    doc_id=str(metadata.get("doc_id") or doc_id or ""),
                )
            )
        return evidence

    def retrieve(
        self,
        query: str,
        doc_id: str | None,
        k: int,
    ) -> list[Evidence]:
        return truncate_evidence(
            self.fetch(query, doc_id, k),
            self.settings.max_evidence_chars,
        )


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

    def rank(
        self,
        query: str,
        doc_id: str | None,
        k: int,
    ) -> list[Evidence]:
        """Rank all matching chunks before applying the top-k cut."""
        candidates = (
            self.chunks
            if doc_id is None
            else [
                chunk
                for chunk in self.chunks
                if chunk.metadata.get("doc_id") in (None, doc_id)
            ]
        )
        scored: list[tuple[float, ChunkRecord]] = []
        for chunk in candidates:
            score = self._score(query, chunk)
            if score > 0:
                scored.append((score, chunk))
        scored.sort(
            key=lambda item: (
                -item[0],
                str(item[1].metadata.get("doc_id") or ""),
                item[1].chunk_id,
            )
        )
        return [
            Evidence(
                chunk_id=chunk.chunk_id,
                text=chunk.text,
                section_path=chunk.section_path,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                score=round(score, 4),
                doc_id=str(chunk.metadata.get("doc_id") or ""),
            )
            for score, chunk in scored[: max(1, k)]
        ]

    def retrieve(
        self,
        query: str,
        doc_id: str | None,
        k: int,
    ) -> list[Evidence]:
        return truncate_evidence(
            self.rank(query, doc_id, k),
            self.max_evidence_chars,
        )


@dataclass
class FallbackTenderRetriever:
    primary: TenderRetriever | None
    fallback: TenderRetriever
    warnings: list[str] = field(default_factory=list)

    def retrieve(
        self,
        query: str,
        doc_id: str | None,
        k: int,
    ) -> list[Evidence]:
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
