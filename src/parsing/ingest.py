"""Embed chunks with Zhipu and upsert them into Chroma."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Iterable

from .config import Settings
from .errors import IngestError
from .models import Chunk

logger = logging.getLogger(__name__)


def _batches(items: list, size: int) -> Iterable[list]:
    size = max(1, size)
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _attr(item, name: str):
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


class ZhipuEmbedder:
    def __init__(self, settings: Settings, client=None) -> None:
        self.settings = settings
        if client is None:
            if not settings.zhipu_api_key:
                raise IngestError("ZHIPU_API_KEY is not configured", skipped=True)
            from zai import ZhipuAiClient

            client = ZhipuAiClient(
                api_key=settings.zhipu_api_key,
                timeout=settings.embedding_timeout,
            )
        self.client = client

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for batch in _batches(list(texts), self.settings.embedding_batch_size):
            response = self._call(batch)
            if len(response) != len(batch):
                raise IngestError(
                    f"embedding count mismatch: expected {len(batch)}, got {len(response)}"
                )
            vectors.extend(response)
        return vectors

    def _call(self, batch: list[str]) -> list[list[float]]:
        last_error: Exception | None = None
        attempts = 3
        for attempt in range(attempts):
            try:
                response = self.client.embeddings.create(
                    input=batch,
                    model=self.settings.embedding_model,
                )
                data = sorted(
                    list(getattr(response, "data", response) or []),
                    key=lambda item: _attr(item, "index") or 0,
                )
                vectors = [list(_attr(item, "embedding") or []) for item in data]
                if not vectors or any(not vector for vector in vectors):
                    raise IngestError("embedding API returned an empty vector")
                return vectors
            except Exception as exc:  # noqa: BLE001 - vendor exception types vary
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(min(2 ** attempt, 4))
        raise IngestError(f"embedding failed after {attempts} attempts: {last_error}")


class ChromaStore:
    def __init__(self, settings: Settings, client=None, collection=None) -> None:
        self.settings = settings
        self.client = client
        self.collection = collection

    def connect(self):
        if self.client is None:
            try:
                import chromadb

                self.client = chromadb.HttpClient(
                    host=self.settings.chroma_host,
                    port=self.settings.chroma_port,
                )
                self.client.heartbeat()
            except Exception as exc:  # noqa: BLE001
                raise IngestError(
                    f"cannot reach Chroma at {self.settings.chroma_host}:{self.settings.chroma_port}: {exc}"
                ) from exc
        return self.client

    def ensure_collection(self, name: str | None = None):
        if self.collection is not None:
            return self.collection
        client = self.connect()
        collection_name = name or self.settings.chroma_collection
        try:
            self.collection = client.get_or_create_collection(
                name=collection_name,
                configuration={"hnsw": {"space": "cosine"}},
                embedding_function=None,
            )
        except Exception:
            self.collection = client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"},
                embedding_function=None,
            )
        return self.collection

    def existing_ids(self, doc_id: str) -> set[str]:
        collection = self.ensure_collection()
        try:
            result = collection.get(where={"doc_id": doc_id}, include=["metadatas"])
        except Exception as exc:  # noqa: BLE001
            raise IngestError(f"cannot query Chroma: {exc}") from exc
        if isinstance(result, dict):
            return set(result.get("ids") or [])
        return set(getattr(result, "ids", []) or [])

    def upsert_chunks(self, chunks: list[Chunk], embeddings: list[list[float]]) -> int:
        collection = self.ensure_collection()
        written = 0
        for batch, vectors in zip(_batches(chunks, 64), _batches(embeddings, 64)):
            try:
                collection.upsert(
                    ids=[chunk.id for chunk in batch],
                    embeddings=vectors,
                    documents=[chunk.embed_text for chunk in batch],
                    metadatas=[chunk.metadata for chunk in batch],
                )
            except Exception as exc:  # noqa: BLE001
                raise IngestError(f"Chroma upsert failed: {exc}") from exc
            written += len(batch)
        return written

    def prune_stale(self, doc_id: str, new_ids: set[str]) -> int:
        existing = self.existing_ids(doc_id)
        stale = sorted(existing - new_ids)
        if stale:
            try:
                self.ensure_collection().delete(ids=stale)
            except Exception as exc:  # noqa: BLE001
                raise IngestError(f"Chroma prune failed: {exc}") from exc
        return len(stale)

    def count(self, doc_id: str | None = None) -> int:
        if doc_id is None:
            return int(self.ensure_collection().count())
        return len(self.existing_ids(doc_id))


def run_ingest(
    chunks: list[Chunk],
    *,
    doc_id: str,
    source_sha256: str,
    settings: Settings,
    store: ChromaStore | None = None,
    embedder: ZhipuEmbedder | None = None,
    collection: str | None = None,
) -> dict:
    if not chunks:
        raise IngestError("no chunks to ingest")
    embedder = embedder or ZhipuEmbedder(settings)
    store = store or ChromaStore(settings)
    if collection:
        store.ensure_collection(collection)

    vectors = embedder.embed([chunk.embed_text for chunk in chunks])
    if len(vectors) != len(chunks):
        raise IngestError("embedding count does not match chunk count")
    before = store.existing_ids(doc_id)
    written = store.upsert_chunks(chunks, vectors)
    deleted = store.prune_stale(doc_id, {chunk.id for chunk in chunks})
    collection_name = (
        getattr(store.ensure_collection(), "name", None)
        or collection
        or settings.chroma_collection
    )
    return {
        "doc_id": doc_id,
        "collection": collection_name,
        "embedding_model": settings.embedding_model,
        "chunk_count": len(chunks),
        "upserted": written,
        "deleted_stale": deleted,
        "existing_before": len(before),
        "source_sha256": source_sha256,
        "chroma": f"{settings.chroma_host}:{settings.chroma_port}",
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }
