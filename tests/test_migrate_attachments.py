from __future__ import annotations

import json
from pathlib import Path

from scripts.migrate_attachments import _migrate
from tests.fakes import make_parse_settings

DOC_ID = "doc-legacy"
PROJECT_ID = "11111111-1111-1111-1111-111111111111"


def test_legacy_attachments_move_to_project_scope(tmp_path: Path) -> None:
    settings = make_parse_settings(tmp_path)
    parsed = settings.parsed_dir / DOC_ID
    parsed.mkdir(parents=True)
    (parsed / "chunks.jsonl").write_text(
        "\n".join(
            [
                json.dumps({
                    "id": f"{DOC_ID}:s1:0000",
                    "text": "主文档",
                    "embed_text": "主文档",
                    "metadata": {"doc_id": DOC_ID},
                }),
                json.dumps({
                    "id": f"{DOC_ID}:att-deadbeef:0000",
                    "text": "附件",
                    "embed_text": "附件",
                    "metadata": {
                        "doc_id": DOC_ID,
                        "chunk_id": f"{DOC_ID}:att-deadbeef:0000",
                        "attachment": "boq.csv",
                        "section_id": "att-deadbeef",
                    },
                }),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (parsed / "attachments.json").write_text(
        json.dumps(
            {
                "attachments": [
                    {
                        "attachment_id": "att-deadbeef",
                        "file_name": "boq.csv",
                        "sha256": "deadbeef",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = _migrate(
        doc_id=DOC_ID,
        project_id=PROJECT_ID,
        settings=settings,
        dry_run=False,
        update_chroma=False,
        collection=None,
    )

    assert result["migrated"] == 1
    assert result["chunks_updated"] == 1
    manifest = parsed / "attachments" / f"{PROJECT_ID}.json"
    assert manifest.is_file()
    assert not (parsed / "attachments.json").exists()
    payloads = [
        json.loads(line)
        for line in (parsed / "chunks.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    attachment = next(
        item for item in payloads if item["metadata"].get("attachment")
    )
    assert attachment["metadata"]["project_id"] == PROJECT_ID
