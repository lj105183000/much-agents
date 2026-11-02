from __future__ import annotations

import csv
import json
from pathlib import Path

from src.evaluation.l2 import (
    HUMAN_HEADERS,
    calibrate_l2,
    export_workbook,
    judge_samples,
    load_human_sheet,
    load_judge_results,
    render_calibration,
)


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _make_run(tmp_path: Path) -> tuple[Path, Path]:
    run_dir = tmp_path / "bids" / "doc-1" / "run-1"
    run_dir.mkdir(parents=True)
    _write(
        run_dir / "outline.json",
        {
            "doc_id": "doc-1",
            "sections": [
                {
                    "id": "narr-1",
                    "title": "施工方案",
                    "kind": "narrative",
                    "selected": True,
                    "requirements": ["包含基坑支护"],
                    "evaluation_factors": [{"factor": "施工方案与技术措施"}],
                },
                {
                    "id": "narr-2",
                    "title": "质量体系",
                    "kind": "narrative",
                    "selected": True,
                    "requirements": ["包含三检制"],
                    "evaluation_factors": [],
                },
            ],
        },
    )
    (run_dir / "sections").mkdir()
    (run_dir / "sections" / "narr-1.md").write_text(
        "本工程采用基坑支护与降水方案。", encoding="utf-8"
    )
    (run_dir / "sections" / "narr-2.md").write_text(
        "质量管理体系实施三检制。", encoding="utf-8"
    )
    _write(
        run_dir / "citations.json",
        {
            "sections": [
                {
                    "section_id": "narr-1",
                    "evidence": [{"chunk_id": "doc-1:s1:0000"}],
                }
            ]
        },
    )
    parsed_dir = tmp_path / "parsed"
    _write(
        parsed_dir / "doc-1" / "chunks.jsonl",
        {"id": "doc-1:s1:0000", "text": "招标文件要求基坑支护。"},
    )
    # The helper above writes pretty JSON, not JSONL; rewrite compact.
    (parsed_dir / "doc-1" / "chunks.jsonl").write_text(
        json.dumps(
            {"id": "doc-1:s1:0000", "text": "招标文件要求基坑支护。"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return run_dir, parsed_dir


def test_export_workbook_builds_samples_and_human_sheet(tmp_path: Path) -> None:
    run_dir, parsed_dir = _make_run(tmp_path)
    result = export_workbook(
        run_dir, parsed_dir=parsed_dir, output_dir=tmp_path / "workbook"
    )
    assert result["samples"] == 2
    samples = [
        json.loads(line)
        for line in (tmp_path / "workbook" / "samples.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    first = next(item for item in samples if item["sample_id"] == "narr-1")
    assert "基坑支护" in first["text"]
    assert "招标文件要求基坑支护" in first["evidence"]
    assert "施工方案与技术措施" in first["requirements"]

    with (tmp_path / "workbook" / "human.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == list(HUMAN_HEADERS)
        rows = list(reader)
    assert len(rows) == 2
    assert rows[0]["verdict"] == ""


def test_judge_samples_uses_blinded_client(tmp_path: Path) -> None:
    class FakeJudge:
        def complete_json(self, **kwargs):
            return {
                "relevance": 4,
                "evidence_support": 4,
                "coverage": 5,
                "structure": 4,
                "factual_safety": 5,
                "issues": [],
                "verdict": "pass",
            }

    results = judge_samples(
        FakeJudge(),
        [
            {
                "sample_id": "narr-1",
                "section_title": "施工方案",
                "requirements": ["基坑支护"],
                "text": "正文",
                "evidence": "证据",
            }
        ],
    )
    assert results[0]["sample_id"] == "narr-1"
    assert results[0]["verdict"] == "pass"
    assert results[0]["overall"] == 4.4


def test_calibration_requires_sample_size_and_agreement(tmp_path: Path) -> None:
    human_path = tmp_path / "human.csv"
    with human_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HUMAN_HEADERS)
        writer.writeheader()
        writer.writerow(
            {
                "sample_id": "narr-1",
                "section_title": "施工方案",
                "verdict": "pass",
                "relevance": "4",
                "evidence_support": "4",
                "coverage": "5",
                "structure": "4",
                "factual_safety": "5",
                "overall": "4.4",
            }
        )
        writer.writerow(
            {
                "sample_id": "narr-2",
                "section_title": "质量体系",
                "verdict": "revise",
                "relevance": "3",
            }
        )
    human, missing = load_human_sheet(human_path)
    assert not missing
    assert human["narr-1"].verdict == "pass"

    judge_path = tmp_path / "judge.jsonl"
    judge_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "sample_id": "narr-1",
                        "verdict": "pass",
                        "scores": {
                            "relevance": 4,
                            "evidence_support": 4,
                            "coverage": 5,
                            "structure": 4,
                            "factual_safety": 5,
                        },
                        "overall": 4.4,
                    }
                ),
                json.dumps(
                    {
                        "sample_id": "narr-2",
                        "verdict": "revise",
                        "scores": {},
                        "overall": None,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    judge = load_judge_results(judge_path)
    report = calibrate_l2(
        human, judge, min_samples=2, min_agreement=0.8, min_kappa=0.6
    )
    assert report["n"] == 2
    assert report["agreement"] == 1.0
    assert report["ready"] is True
    assert report["dimensions"]["relevance"]["mae"] == 0.0
    assert "L2 judge 校准报告" in render_calibration(report)

    judge["narr-2"]["verdict"] = "pass"
    failed = calibrate_l2(
        human, judge, min_samples=2, min_agreement=0.8, min_kappa=0.6
    )
    assert failed["ready"] is False
    assert failed["agreement"] == 0.5
