from __future__ import annotations

from src.parsing.chunking import chunk_document
from src.parsing.config import Settings
from src.parsing.errors import IngestError
from src.parsing.ingest import ChromaStore, ZhipuEmbedder, run_ingest
from src.parsing.models import ParsedDocument


class FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, float(len(text) % 7)] for text in texts]


class FakeCollection:
    name = "fake_collection"

    def __init__(self, existing: list[str] | None = None) -> None:
        self.ids = list(existing or [])
        self.upserted: dict[str, list[float]] = {}
        self.deleted: list[str] = []

    def get(self, where=None, include=None):
        return {"ids": list(self.ids)}

    def upsert(self, ids, embeddings, documents, metadatas):
        for chunk_id, vector in zip(ids, embeddings):
            self.upserted[chunk_id] = vector
        self.ids = list(dict.fromkeys([*self.ids, *ids]))

    def delete(self, ids):
        self.deleted.extend(ids)
        self.ids = [item for item in self.ids if item not in set(ids)]

    def count(self):
        return len(self.ids)


def test_run_ingest_upserts_and_prunes(settings: Settings, document: ParsedDocument) -> None:
    chunks = chunk_document(document, size=200, overlap=30, min_size=30)
    stale = f"{document.doc_id}:s000:9999"
    collection = FakeCollection(existing=[stale])
    store = ChromaStore(settings, collection=collection)
    summary = run_ingest(
        chunks,
        doc_id=document.doc_id,
        source_sha256="sha",
        settings=settings,
        store=store,
        embedder=FakeEmbedder(),
    )
    assert summary["chunk_count"] == len(chunks)
    assert summary["upserted"] == len(chunks)
    assert summary["deleted_stale"] == 1
    assert collection.deleted == [stale]
    assert set(collection.upserted) == {chunk.id for chunk in chunks}


def test_rerun_is_idempotent(settings: Settings, document: ParsedDocument) -> None:
    chunks = chunk_document(document, size=200, overlap=30, min_size=30)
    collection = FakeCollection()
    store = ChromaStore(settings, collection=collection)
    run_ingest(
        chunks, doc_id=document.doc_id, source_sha256="sha", settings=settings,
        store=store, embedder=FakeEmbedder(),
    )
    run_ingest(
        chunks, doc_id=document.doc_id, source_sha256="sha", settings=settings,
        store=store, embedder=FakeEmbedder(),
    )
    assert len(collection.upserted) == len(chunks)
    assert collection.deleted == []


def test_missing_zhipu_key_skips() -> None:
    settings = Settings.from_env(env={})
    try:
        ZhipuEmbedder(settings)
    except IngestError as exc:
        assert exc.skipped is True
    else:  # pragma: no cover
        raise AssertionError("expected IngestError")


class FakeChromaClient:
    def __init__(self) -> None:
        self.kwargs = None

    def heartbeat(self):
        return 1

    def get_or_create_collection(self, name, **kwargs):
        if "configuration" in kwargs:
            raise ValueError("configuration unsupported")
        self.kwargs = kwargs
        return FakeCollection()


def test_collection_configuration_fallback(settings: Settings) -> None:
    client = FakeChromaClient()
    store = ChromaStore(settings, client=client)
    collection = store.ensure_collection()
    assert collection.name == "fake_collection"
    assert "metadata" in client.kwargs
    assert client.kwargs["embedding_function"] is None
