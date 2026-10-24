from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from src.bidding.checks import build_allowed
from src.bidding.figures import planner as figure_planner
from src.bidding.figures import render as figure_render
from src.bidding.figures.planner import FigurePlanner, parse_date, parse_duration_days
from src.bidding.figures.render import FigureRenderError, render_figure
from src.bidding.figures.rules import FIGURE_RULES, select_rules
from src.bidding.figures.schema import FigureManifest, FigureSpec
from src.bidding.figures.service import (
    build_figure_blocks,
    figure_report,
    generate_figures,
    read_manifest,
)
from src.bidding.plan import plan_bid
from src.bidding.profile import BidderProfile
from src.bidding.staged_output import SectionOutput, assemble_staged_bid
from tests.fakes import make_bid_settings, write_parsed_fixture

PROFILE = {
    "company": {"name": "测试建设有限公司"},
    "project_manager": {"name": "王五"},
    "personnel": [
        {"role": "技术负责人", "name": "赵六"},
        {"role": "安全负责人", "name": "钱七"},
    ],
}


def _spec(**changes) -> dict:
    base = {
        "figure_id": "fig-01-org_chart",
        "section_id": "top-07",
        "section_title": "项目管理机构",
        "kind": "org_chart",
        "title": "项目组织机构图",
        "caption": "图 1 项目组织机构图",
        "disclaimer": "依据投标人资料绘制",
        "source": "profile",
        "file": "figures/fig-01-org_chart.png",
        "source_hash": "abc",
    }
    base.update(changes)
    return base


def test_figure_spec_rejects_unsafe_paths_and_unknown_kinds() -> None:
    FigureSpec.model_validate(_spec())
    for path in (
        "../escape.png",
        "figures/../escape.png",
        "figures\\escape.png",
        "figures/not-image.svg",
        "C:/figures/a.png",
    ):
        with pytest.raises(ValidationError):
            FigureSpec.model_validate(_spec(file=path))
    with pytest.raises(ValidationError):
        FigureSpec.model_validate(_spec(kind="freeform"))
    with pytest.raises(ValidationError):
        FigureSpec.model_validate(_spec(unknown="x"))


def test_manifest_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        FigureManifest.model_validate(
            {"generated_at": "2024-01-01T00:00:00+00:00", "unexpected": True}
        )


@pytest.mark.parametrize(
    ("kind", "data"),
    [
        (
            "org_chart",
            {
                "company": "测试公司",
                "manager": {"role": "项目经理", "name": "王五"},
                "members": [{"role": "技术负责人", "name": "赵六"}],
            },
        ),
        ("process_flow", {"steps": ["施工准备", "测量放线", "土方开挖"]}),
        (
            "gantt",
            {
                "mode": "days",
                "start_text": "第 1 天",
                "end_text": "第 100 天",
                "total_days": 100,
                "phases": [
                    {"name": "施工准备", "start_day": 0, "duration_days": 10},
                    {"name": "主要施工", "start_day": 10, "duration_days": 75},
                    {"name": "收尾验收", "start_day": 85, "duration_days": 15},
                ],
            },
        ),
        (
            "site_plan",
            {
                "zones": [
                    {"name": "办公区", "kind": "office"},
                    {"name": "施工区", "kind": "construction"},
                ]
            },
        ),
    ],
)
def test_all_figure_kinds_render_png(tmp_path: Path, kind: str, data: dict) -> None:
    target = tmp_path / f"{kind}.png"
    result = render_figure(
        kind=kind,
        data=data,
        title="测试图",
        disclaimer="本图为示意图。",
        output_path=target,
    )
    assert result.path.is_file()
    assert target.stat().st_size > 2_000
    assert (result.width, result.height) == (1800, result.height)
    assert target.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_bold_font_falls_back_to_regular(tmp_path: Path, monkeypatch) -> None:
    regular = tmp_path / "cjk.ttc"
    regular.write_bytes(b"path-only")

    def fake_find(explicit: str | None, *, bold: bool = False) -> Path:
        if bold:
            raise FigureRenderError("no bold variant")
        return regular

    monkeypatch.setattr(figure_render, "_find_font", fake_find)
    fonts = figure_render._Fonts(None)
    assert fonts.regular_path == regular
    assert fonts.bold_path == regular


def test_render_fails_cleanly_without_cjk_font(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(figure_render, "_FONT_CANDIDATES", ())
    monkeypatch.setattr(figure_render, "_BOLD_CANDIDATES", ())
    monkeypatch.setattr(figure_render, "FONT_ENV", "TEST_MISSING_FIGURE_FONT")
    monkeypatch.delenv("TEST_MISSING_FIGURE_FONT", raising=False)
    with pytest.raises(FigureRenderError):
        render_figure(
            kind="process_flow",
            data={"steps": ["甲", "乙", "丙"]},
            title="测试",
            disclaimer="示意",
            output_path=tmp_path / "missing.png",
        )


def _planned(tmp_path: Path):
    write_parsed_fixture(tmp_path)
    settings = make_bid_settings(
        tmp_path,
        BID_FIGURES_ENABLED="true",
        BID_FIGURES_USE_LLM="false",
    )
    planned = plan_bid("doc-1", settings, max_sections=2)
    profile = BidderProfile.from_mapping(PROFILE)
    narratives = planned.outline.selected_narratives
    assert len(narratives) == 2
    outputs = {
        narratives[0].id: SectionOutput(
            section_id=narratives[0].id,
            status="final",
            final_markdown="项目名称：测试供水工程\n",
        ),
        narratives[1].id: SectionOutput(
            section_id=narratives[1].id,
            status="final",
            final_markdown=(
                "1. 施工准备\n2. 测量放线\n3. 土方开挖\n"
                "4. 基础施工\n5. 竣工验收\n"
            ),
        ),
    }
    allowed = build_allowed(planned.context, profile, planned.outline)
    assembly = assemble_staged_bid(
        context=planned.context,
        profile=profile,
        outline=planned.outline,
        outputs=outputs,
        allowed=allowed,
    )
    return planned, profile, outputs, assembly, settings


def test_generate_figures_reuses_unchanged_manifest(tmp_path: Path) -> None:
    planned, profile, outputs, assembly, settings = _planned(tmp_path)
    run_dir = tmp_path / "run"
    kwargs = dict(
        context=planned.context,
        profile=profile,
        outline=planned.outline,
        section_markdowns=assembly.section_markdowns,
        outputs=outputs,
        run_dir=run_dir,
        settings=settings,
    )
    first = generate_figures(**kwargs)
    assert first.enabled is True
    assert first.stats["generated"] == 4
    assert first.stats["failed"] == 0
    assert first.stats["reused"] == 0
    assert {spec.kind for spec in first.figures} == {
        "org_chart",
        "process_flow",
        "gantt",
        "site_plan",
    }
    assert all((run_dir / spec.file).is_file() for spec in first.figures)
    first_bytes = {
        spec.figure_id: (run_dir / spec.file).read_bytes() for spec in first.figures
    }

    second = generate_figures(**kwargs)
    assert second.stats["reused"] == 4
    assert second.stats["generated"] == 4
    assert {
        spec.figure_id: (run_dir / spec.file).read_bytes() for spec in second.figures
    } == first_bytes

    stored = read_manifest(run_dir)
    assert stored is not None
    assert stored.stats["reused"] == 4


def test_figure_blocks_replace_attachment_placeholders(tmp_path: Path) -> None:
    planned, profile, outputs, assembly, settings = _planned(tmp_path)
    manifest = generate_figures(
        context=planned.context,
        profile=profile,
        outline=planned.outline,
        section_markdowns=assembly.section_markdowns,
        outputs=outputs,
        run_dir=tmp_path / "run",
        settings=settings,
    )
    blocks = build_figure_blocks(manifest)
    assert len(blocks) == 4
    gantt = next(block for block in blocks if block.section_id == "att-06-04")
    site = next(block for block in blocks if block.section_id == "att-06-05")
    assert gantt.replace_text == "【待填写：施工进度网络图/横道图】"
    assert site.replace_text == "【待填写：施工总平面图】"
    assert "![图 " in gantt.markdown
    assert "](#)" not in gantt.markdown
    assert "示意图" in site.markdown
    report = figure_report(manifest)
    assert report["stats"]["generated"] == 4
    assert report["enabled"] is True


def test_disabled_figures_write_empty_manifest(tmp_path: Path) -> None:
    planned, profile, outputs, assembly, settings = _planned(tmp_path)
    disabled = make_bid_settings(
        tmp_path,
        BID_FIGURES_ENABLED="false",
        BID_FIGURES_USE_LLM="false",
    )
    manifest = generate_figures(
        context=planned.context,
        profile=profile,
        outline=planned.outline,
        section_markdowns=assembly.section_markdowns,
        outputs=outputs,
        run_dir=tmp_path / "run",
        settings=disabled,
    )
    assert manifest.enabled is False
    assert manifest.figures == []
    assert manifest.stats["generated"] == 0
    assert build_figure_blocks(manifest) == []
    assert json.loads((tmp_path / "run" / "figures" / "manifest.json").read_text("utf-8"))[
        "enabled"
    ] is False


def test_planner_only_uses_available_facts(tmp_path: Path) -> None:
    planned, profile, _outputs, assembly, settings = _planned(tmp_path)
    planner = FigurePlanner(settings)
    by_kind = {rule.kind: (section, rule) for section, rule in select_rules(
        planned.outline, max_figures=4
    )}
    org_section, org_rule = by_kind["org_chart"]
    org = planner.plan(
        section=org_section,
        rule=org_rule,
        body=assembly.section_markdowns[org_section.id],
        context=planned.context,
        profile=profile,
    )
    assert org is not None
    assert org.data["company"] == "测试建设有限公司"
    assert org.data["manager"]["name"] == "王五"
    assert "赵六" in {member["name"] for member in org.data["members"]}

    empty = planner.plan(
        section=org_section,
        rule=org_rule,
        body=assembly.section_markdowns[org_section.id],
        context=planned.context,
        profile=BidderProfile.empty(),
    )
    assert empty is None

    gantt_section, gantt_rule = by_kind["gantt"]
    gantt = planner.plan(
        section=gantt_section,
        rule=gantt_rule,
        body=assembly.section_markdowns[gantt_section.id],
        context=planned.context,
        profile=profile,
    )
    assert gantt is not None
    assert gantt.data["total_days"] == 218
    assert any("示意" in warning for warning in gantt.warnings)


def test_llm_steps_must_be_verbatim() -> None:
    class FakeLLM:
        def complete_json(self, **_kwargs):
            return {
                "steps": [
                    {"label": "施工准备", "quote": "施工准备"},
                    {"label": "测量放线", "quote": "测量放线"},
                    {"label": "不存在的步骤", "quote": "不存在的步骤"},
                    {"label": "土方开挖", "quote": "土方开挖"},
                ]
            }

    from src.bidding.llm import ChatClient

    llm: ChatClient = FakeLLM()  # type: ignore[assignment]
    text = "1. 施工准备\n2. 测量放线\n3. 土方开挖"
    steps = figure_planner._llm_steps(  # noqa: SLF001 - focused unit test
        llm,
        section=type("Section", (), {"title": "施工方案"})(),
        markdown=text,
        max_tokens=500,
    )
    assert steps == ["施工准备", "测量放线", "土方开挖"]


def test_parse_date_and_duration_ignore_placeholders() -> None:
    assert parse_date("计划开工日期：2023年11月25日") is not None
    assert parse_date("2023/11/25") is not None
    assert parse_date("【待填写：日期】") is None
    assert parse_duration_days("218") == 218
    assert parse_duration_days("计划工期 218 日历天") == 218
    assert parse_duration_days("【待填写：工期】") is None
    assert len(FIGURE_RULES) == 4


def test_external_renderer_marks_source_and_falls_back_on_failure(
    tmp_path: Path,
) -> None:
    planned, profile, outputs, assembly, _settings = _planned(tmp_path)
    settings = make_bid_settings(
        tmp_path,
        BID_FIGURES_EXTERNAL="true",
        BID_FIGURES_USE_LLM="false",
    )
    seen: list[dict] = []

    def renderer(spec: dict, target: Path) -> str:
        seen.append(spec)
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (240, 120), "white").save(target, format="PNG")
        return "mcp:drawing/render_site_plan"

    manifest = generate_figures(
        context=planned.context,
        profile=profile,
        outline=planned.outline,
        section_markdowns=assembly.section_markdowns,
        outputs=outputs,
        run_dir=tmp_path / "external",
        settings=settings,
        external_renderer=renderer,
    )
    assert {spec["kind"] for spec in seen} == {"process_flow", "site_plan"}
    assert all("profile" not in json.dumps(spec, ensure_ascii=False) for spec in seen)
    external = [spec for spec in manifest.figures if spec.external]
    assert {spec.kind for spec in external} == {"process_flow", "site_plan"}
    assert all(spec.source == "mcp:drawing/render_site_plan" for spec in external)
    assert manifest.stats["external"] == 2
    assert all("外部绘图工具" in spec.disclaimer for spec in external)

    def failing(_spec: dict, _target: Path) -> str:
        raise RuntimeError("drawing server unavailable")

    fallback = generate_figures(
        context=planned.context,
        profile=profile,
        outline=planned.outline,
        section_markdowns=assembly.section_markdowns,
        outputs=outputs,
        run_dir=tmp_path / "fallback",
        settings=settings,
        external_renderer=failing,
        force=True,
    )
    assert fallback.stats["external"] == 0
    assert fallback.stats["generated"] == 4
    assert all((tmp_path / "fallback" / spec.file).is_file() for spec in fallback.figures)
    assert any(
        "外部绘图工具失败" in warning
        for spec in fallback.figures
        for warning in spec.warnings
    )
