"""Render a human review checklist for a draft eval case."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .dataset import EvalDatasetError


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EvalDatasetError(f"missing file: {path}") from exc
    except ValueError as exc:
        raise EvalDatasetError(f"invalid JSON: {path}: {exc}") from exc


def render_review_checklist(case_id: str, data_dir: Path) -> str:
    labels = data_dir / "labels" / case_id
    factors = _load(labels / "evaluation_factors.json")
    points = _load(labels / "must_cover_points.json") if (labels / "must_cover_points.json").is_file() else {}
    facts = _load(labels / "key_facts.json") if (labels / "key_facts.json").is_file() else {}
    factor_items = factors.get("factors") if isinstance(factors, dict) else []
    point_items = points.get("points") if isinstance(points, dict) else []
    fact_items = facts.get("facts") if isinstance(facts, dict) else []

    by_factor: dict[str, list[dict[str, Any]]] = {}
    for point in point_items or []:
        if isinstance(point, dict):
            by_factor.setdefault(str(point.get("factor_id") or ""), []).append(point)

    lines = [
        f"# 评测集人工复核：{case_id}",
        "",
        "> 本清单由 draft 标签自动生成。请逐项确认评分因素、候选覆盖点和关键事实，",
        "> 修改后在 `must_cover_points.json` 中把 `status` 改为 `reviewed`。",
        "",
        "## 1. 评分因素",
        "",
        "| ID | 类别 | 因素 | 分值 | 评分细则 |",
        "|---|---|---|---:|---|",
    ]
    for factor in factor_items or []:
        lines.append(
            "| {id} | {category} | {factor} | {score} | {criteria} |".format(
                id=factor.get("id"),
                category=factor.get("category") or "",
                factor=factor.get("factor") or "",
                score=factor.get("max_score") if factor.get("max_score") is not None else "—",
                criteria=str(factor.get("scoring_criteria") or "").replace("|", "\\|"),
            )
        )
    lines += ["", "## 2. 候选必须覆盖点", ""]
    for factor in factor_items or []:
        factor_id = str(factor.get("id") or "")
        candidates = by_factor.get(factor_id) or []
        lines.append(f"### {factor_id} {factor.get('factor') or ''}")
        if not candidates:
            lines.append("- [ ] **没有自动候选点，需要人工补充**")
        for point in candidates:
            lines.append(f"- [ ] {point.get('text')}（{point.get('id')}）")
        lines.append("")
    lines += ["## 3. 关键事实", ""]
    for fact in fact_items or []:
        lines.append(
            "- [ ] {label}：{value}（页码 {page}）".format(
                label=fact.get("label") or fact.get("key"),
                value=fact.get("value"),
                page=fact.get("source_page") if fact.get("source_page") is not None else "—",
            )
        )
    lines += [
        "",
        "## 4. 复核结论",
        "",
        "- [ ] 评分因素已确认",
        "- [ ] 覆盖点已合并/改写，无重复或不可判断项",
        "- [ ] 关键事实已核对，无编造/过期数据",
        "- [ ] 已将 `must_cover_points.json` 的 `status` 改为 `reviewed`",
        "",
    ]
    return "\n".join(lines)


def export_review_checklist(
    data_dir: Path, case_id: str, output_dir: Path | None = None
) -> Path:
    target_dir = output_dir or (data_dir / "reviews")
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{case_id}.md"
    path.write_text(render_review_checklist(case_id, data_dir), encoding="utf-8")
    return path


__all__ = ["export_review_checklist", "render_review_checklist"]
