from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

from src.bidding.plan import plan_bid
from src.bidding.requirements import build_requirements
from src.parsing.extraction.schemas import (
    EvaluationFactor,
    QualificationRequirement,
    StructuredExtraction,
)
from tests.fakes import make_bid_settings, write_parsed_fixture


def _outline(tmp_path: Path):
    write_parsed_fixture(tmp_path)
    settings = make_bid_settings(tmp_path)
    return plan_bid("doc-1", settings, max_sections=1).outline


def test_requirements_register_maps_and_scores_coverage(tmp_path: Path) -> None:
    outline = _outline(tmp_path)
    narrative = outline.selected_narratives[0]
    extraction = StructuredExtraction(
        evaluation_factors=[
            EvaluationFactor(
                category="施工组织设计",
                factor="施工组织设计",
                max_score=4,
                source_page=3,
            ),
            EvaluationFactor(
                category="其他评分因素",
                factor="未映射因素",
                max_score=2,
            ),
        ],
        qualification_requirements=[
            QualificationRequirement(
                category="资格要求",
                requirement="投标人须具备有效的营业执照和资质证书。",
                source_page=8,
            ),
            QualificationRequirement(
                category="资格要求",
                requirement="无需提供联合体协议。",
            ),
        ],
    )
    context = SimpleNamespace(extraction=extraction)
    result = build_requirements(
        run_id="run-1",
        context=context,  # type: ignore[arg-type]
        outline=outline,
        section_markdowns={
            narrative.id: "施工组织设计正文满足要求。",
            "form-09-01": "【待填写：营业执照】",
        },
    )
    items = {item["id"]: item for item in result["items"]}
    assert items["REQ-EVAL-001"]["status"] == "covered"
    assert items["REQ-EVAL-001"]["max_score"] == 4
    assert items["REQ-EVAL-002"]["status"] == "missing"
    assert items["REQ-QUAL-001"]["status"] == "partial"
    assert items["REQ-QUAL-001"]["mandatory"] is True
    assert items["REQ-QUAL-002"]["status"] == "not_applicable"
    assert result["summary"]["covered"] == 1
    assert result["summary"]["partial"] == 1
    assert result["summary"]["missing"] == 1
    assert result["summary"]["not_applicable"] == 1
    assert result["summary"]["mandatory_missing"] == 0
    assert result["summary"]["weighted_coverage"] < 1.0


def test_narrative_coverage_requires_requirement_evidence(
    tmp_path: Path,
) -> None:
    outline = _outline(tmp_path)
    targets = [section for section in outline.sections if section.kind == "narrative"]
    section = targets[2]  # 6.3 质量管理体系与措施
    factor = EvaluationFactor(
        category="施工组织设计",
        factor="质量管理体系与措施",
        max_score=3,
    )
    section.selected = True
    section.evaluation_factors = [
        {"category": factor.category, "factor": factor.factor}
    ]
    extraction = StructuredExtraction(evaluation_factors=[factor])
    context = SimpleNamespace(extraction=extraction)

    unrelated = build_requirements(
        run_id="run-1",
        context=context,  # type: ignore[arg-type]
        outline=outline,
        section_markdowns={section.id: "本章介绍施工准备和现场布置。"},
    )
    assert unrelated["items"][0]["status"] != "covered"
    assert unrelated["items"][0]["coverage_evidence"][0]["matched_terms"] == []

    covered = build_requirements(
        run_id="run-1",
        context=context,  # type: ignore[arg-type]
        outline=outline,
        section_markdowns={
            section.id: "本章建立质量管理体系，明确质量目标和检查措施。[E1]"
        },
    )
    assert covered["items"][0]["status"] == "covered"
    evidence = covered["items"][0]["coverage_evidence"][0]
    assert evidence["matched_terms"]
    assert evidence["citations"] == ["[E1]"]


def test_mandatory_missing_is_counted(tmp_path: Path) -> None:
    outline = _outline(tmp_path)
    extraction = StructuredExtraction(
        qualification_requirements=[
            QualificationRequirement(
                category="资格要求",
                requirement="投标人须提供有效的营业执照。",
            )
        ]
    )
    result = build_requirements(
        run_id="run-1",
        context=SimpleNamespace(extraction=extraction),  # type: ignore[arg-type]
        outline=outline,
        section_markdowns={},
    )
    assert result["summary"]["mandatory_missing"] == 1
    assert result["items"][0]["status"] == "missing"
    assert any("未抽取到评分因素" in warning for warning in result["warnings"])
