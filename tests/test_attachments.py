from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest

from src.bidding.context import ChunkRecord
from src.bidding.retrieval import KeywordTenderRetriever
from src.parsing.attachments import (
    AttachmentError,
    append_attachment_chunks,
    attach_evidence,
    build_attachment_chunks,
    detach_evidence,
    list_attachments,
)
from src.parsing.config import Settings

DOC_ID = "doc-att"
MAIN_CHUNK = {
    "id": f"{DOC_ID}:s000:0000",
    "text": "主体招标文件内容",
    "embed_text": "主体招标文件内容",
    "metadata": {
        "doc_id": DOC_ID,
        "chunk_id": f"{DOC_ID}:s000:0000",
        "section_path": "封面",
    },
}


def _parsed_doc(settings: Settings) -> Path:
    parsed = settings.parsed_dir / DOC_ID
    parsed.mkdir(parents=True, exist_ok=True)
    (parsed / "chunks.jsonl").write_text(
        json.dumps(MAIN_CHUNK, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (parsed / "extraction.json").write_text(
        json.dumps(
            {"project_overview": {"project_name": "测试供水工程"}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return parsed


def _csv(path: Path) -> Path:
    path.write_text(
        "项目编码,项目名称,计量单位,工程量\n"
        "010101001,沟槽土方开挖,m3,1200\n"
        "030101002,PE100给水管DN200,m,5000\n"
        "备注,水压试验压力0.8MPa，回填压实度0.95，沟槽支护钢板桩,,,\n",
        encoding="utf-8",
    )
    return path


def _pdf(path: Path) -> Path:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "BOQ 010101001 DN200 0.8MPa")
    page.insert_text((72, 100), "Backfill compaction 0.95")
    document.save(path)
    document.close()
    return path


def _blank_pdf(path: Path) -> Path:
    document = pymupdf.open()
    document.new_page()
    document.save(path)
    document.close()
    return path


class _FakeOCR:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, str]] = []

    def parse(self, pdf_path: Path, doc_id: str, **kwargs):
        self.calls.append((pdf_path, doc_id))
        return SimpleNamespace(
            page_count=1,
            content_list=[
                SimpleNamespace(
                    type="table",
                    text="",
                    table_body="项目编码 | 010101001 | DN200",
                    page=1,
                )
            ],
            markdown="",
        )


def test_ocr_fallback_for_scanned_pdf(tmp_path: Path) -> None:
    path = _blank_pdf(tmp_path / "scanned.pdf")
    parser = _FakeOCR()

    chunks, meta = build_attachment_chunks(
        DOC_ID,
        path,
        chunk_size=1000,
        ocr_parser=parser,
    )

    assert chunks
    assert "010101001" in "\n".join(chunk.text for chunk in chunks)
    assert meta["parser"] == "mineru"
    assert any("MinerU OCR fallback" in item for item in meta["warnings"])
    assert parser.calls


def test_scanned_pdf_without_ocr_raises(tmp_path: Path) -> None:
    path = _blank_pdf(tmp_path / "scanned.pdf")
    with pytest.raises(AttachmentError):
        build_attachment_chunks(DOC_ID, path)


def test_detach_removes_attachment_and_keeps_main(
    tmp_path: Path, settings: Settings
) -> None:
    parsed = _parsed_doc(settings)
    path = _csv(tmp_path / "boq.csv")
    result = attach_evidence(
        DOC_ID, path, settings, ingest=False, sync_storage=False
    )
    assert len(list_attachments(DOC_ID, settings)["attachments"]) == 1

    detached = detach_evidence(
        DOC_ID,
        result["attachment_id"],
        settings,
        sync_storage=False,
        delete_chroma=False,
    )

    assert detached["removed_chunks"] == result["chunk_count"]
    assert detached["remaining_attachments"] == 0
    payloads = [
        json.loads(line)
        for line in (parsed / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [item["id"] for item in payloads] == [MAIN_CHUNK["id"]]
    assert list_attachments(DOC_ID, settings)["attachments"] == []


def test_detach_deletes_chroma_vectors(
    tmp_path: Path, settings: Settings
) -> None:
    _parsed_doc(settings)
    path = _csv(tmp_path / "boq.csv")
    result = attach_evidence(
        DOC_ID, path, settings, ingest=False, sync_storage=False
    )

    class FakeCollection:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        def delete(self, ids):
            self.deleted.extend(ids)

    class FakeStore:
        def __init__(self) -> None:
            self.collection = FakeCollection()

        def ensure_collection(self, name=None):
            return self.collection

    store = FakeStore()
    detached = detach_evidence(
        DOC_ID,
        result["attachment_id"],
        settings,
        sync_storage=False,
        delete_chroma=True,
        store=store,
    )

    assert detached["chroma_deleted"] == result["chunk_count"]
    assert len(store.collection.deleted) == result["chunk_count"]


def test_csv_attachment_chunks(tmp_path: Path) -> None:
    path = _csv(tmp_path / "boq.csv")
    chunks, meta = build_attachment_chunks(
        DOC_ID,
        path,
        project_name="测试供水工程",
        chunk_size=1000,
        overlap=100,
    )

    assert chunks
    assert all(chunk.id.startswith(f"{DOC_ID}:att-") for chunk in chunks)
    assert all(chunk.metadata["section_path"] == "附件：boq.csv" for chunk in chunks)
    assert meta["chunk_count"] == len(chunks)
    combined = "\n".join(chunk.text for chunk in chunks)
    assert "010101001" in combined
    assert "DN200" in combined
    assert "0.8MPa" in combined


def test_pdf_attachment_chunks(tmp_path: Path) -> None:
    path = _pdf(tmp_path / "evidence.pdf")
    chunks, meta = build_attachment_chunks(DOC_ID, path, chunk_size=1000, overlap=100)

    assert chunks
    assert meta["parser"] == "pymupdf"
    assert any("010101001" in chunk.text for chunk in chunks)


def test_append_is_idempotent_and_keeps_main_chunks(
    tmp_path: Path, settings: Settings
) -> None:
    parsed = _parsed_doc(settings)
    path = _csv(tmp_path / "boq.csv")
    chunks, meta = build_attachment_chunks(DOC_ID, path, chunk_size=1000)

    for _ in range(2):
        append_attachment_chunks(
            parsed,
            doc_id=DOC_ID,
            attachment_id=meta["attachment_id"],
            chunks=chunks,
            meta=meta,
        )

    payloads = [
        json.loads(line)
        for line in (parsed / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert sum(1 for item in payloads if item["id"] == MAIN_CHUNK["id"]) == 1
    attachment_items = [
        item
        for item in payloads
        if item["id"].startswith(f"{DOC_ID}:{meta['attachment_id']}:")
    ]
    assert len(attachment_items) == len(chunks)
    manifest = json.loads((parsed / "attachments.json").read_text(encoding="utf-8"))
    assert len(manifest["attachments"]) == 1
    assert manifest["attachments"][0]["sha256"] == meta["sha256"]


def test_keyword_retrieval_finds_attachment_chunks(
    tmp_path: Path, settings: Settings
) -> None:
    parsed = _parsed_doc(settings)
    path = _csv(tmp_path / "boq.csv")
    chunks, meta = build_attachment_chunks(DOC_ID, path, chunk_size=1000)
    append_attachment_chunks(
        parsed,
        doc_id=DOC_ID,
        attachment_id=meta["attachment_id"],
        chunks=chunks,
        meta=meta,
    )
    records = [
        ChunkRecord(
            chunk_id=item["id"],
            text=item["text"],
            embed_text=item["embed_text"],
            metadata=item["metadata"],
        )
        for item in (
            json.loads(line)
            for line in (parsed / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    ]

    hits = KeywordTenderRetriever(records).retrieve(
        "项目编码 管径 水压试验 回填压实度", DOC_ID, 5
    )

    assert hits
    assert any("010101001" in item.text or "DN200" in item.text for item in hits)


def test_attach_evidence_without_ingest(
    tmp_path: Path, settings: Settings
) -> None:
    _parsed_doc(settings)
    path = _csv(tmp_path / "boq.csv")

    result = attach_evidence(
        DOC_ID,
        path,
        settings,
        ingest=False,
        sync_storage=False,
    )

    assert result["ok"] is True
    assert result["chunk_count"] > 0
    assert result["ingested"] is None
    assert result["storage_synced"] is False


def test_unsupported_attachment_type(tmp_path: Path) -> None:
    path = tmp_path / "boq.xlsx"
    path.write_bytes(b"not a real xlsx")
    with pytest.raises(AttachmentError):
        build_attachment_chunks(DOC_ID, path)
