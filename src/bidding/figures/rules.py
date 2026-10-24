"""Fixed section-to-figure rules.

The planner may decide *whether* a rule can be satisfied from available facts,
but it never invents a new section target or a new figure kind.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import BidOutline, OutlineSection
from .schema import FigureKind

SCHEDULE_PLACEHOLDER = "【待填写：施工进度网络图/横道图】"
SITE_PLAN_PLACEHOLDER = "【待填写：施工总平面图】"


@dataclass(frozen=True, slots=True)
class FigureRule:
    section_id: str
    template_key: str
    kind: FigureKind
    title: str
    disclaimer: str
    replace_text: str | None = None


FIGURE_RULES: tuple[FigureRule, ...] = (
    FigureRule(
        section_id="top-07",
        template_key="project_management",
        kind="org_chart",
        title="项目组织机构图",
        disclaimer=(
            "依据投标人提供的公司及人员资料绘制；标注为待填写的节点须人工补充，"
            "未提供人员不作推定。"
        ),
    ),
    FigureRule(
        section_id="att-06-04",
        template_key="attachment_schedule",
        kind="gantt",
        title="施工进度计划横道图",
        disclaimer=(
            "图中阶段划分为示意，具体开工、完工日期及进度安排以招标文件和"
            "经批准的施工进度计划为准。"
        ),
        replace_text=SCHEDULE_PLACEHOLDER,
    ),
    FigureRule(
        section_id="att-06-05",
        template_key="attachment_site_plan",
        kind="site_plan",
        title="施工总平面布置示意图",
        disclaimer=(
            "本图为施工总平面布置示意，非现场实测；具体布置须项目技术负责人"
            "结合现场条件复核。"
        ),
        replace_text=SITE_PLAN_PLACEHOLDER,
    ),
    FigureRule(
        section_id="narr-06-02",
        template_key="",
        kind="process_flow",
        title="主要施工工艺流程图",
        disclaimer=(
            "本图依据本章节正文提取的施工步骤绘制，具体工艺参数以专项施工方案"
            "及经批准的施工组织设计为准。"
        ),
    ),
)


def select_rules(
    outline: BidOutline,
    *,
    max_figures: int,
) -> list[tuple[OutlineSection, FigureRule]]:
    """Return applicable rules in document order, capped by max_figures."""
    by_id = {section.id: section for section in outline.sections}
    by_template = {
        section.template_key: section
        for section in outline.sections
        if section.template_key
    }
    selected: list[tuple[OutlineSection, FigureRule]] = []
    seen: set[str] = set()
    for rule in FIGURE_RULES:
        section = by_id.get(rule.section_id) or by_template.get(rule.template_key)
        if section is None or section.id in seen or not section.selected:
            continue
        if section.kind == "narrative" and not section.selected:
            continue
        seen.add(section.id)
        selected.append((section, rule))
    selected.sort(key=lambda item: (item[0].order, item[0].id))
    limit = max(0, int(max_figures))
    return selected[:limit] if limit else []


__all__ = [
    "FIGURE_RULES",
    "FigureRule",
    "SCHEDULE_PLACEHOLDER",
    "SITE_PLAN_PLACEHOLDER",
    "select_rules",
]
