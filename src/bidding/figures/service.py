"""Figure planning, rendering, reuse and Markdown injection helpers."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from src.parsing.artifacts import atomic_write_json, ensure_dir, read_json

from ..config import BidSettings
from ..context import TenderContext
from ..llm import ChatClient
from ..models import BidOutline, OutlineSection
from ..profile import BidderProfile
from ..staged_output import SectionOutput
from .planner import FigurePlanner
from .render import FigureRenderError, render_figure
from .rules import FIGURE_RULES, select_rules
from .schema import FigureBlock, FigureManifest, FigureSpec

logger = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"
FIGURE_VERSION = "figures-v1"
_EXTERNAL_KINDS = frozenset({"site_plan", "process_flow"})
ExternalRenderer = Callable[[dict[str, Any], Path], bool]


def figures_dir(run_dir: str | Path) -> Path:
    return Path(run_dir) / "figures"


def manifest_path(run_dir: str | Path) -> Path:
    return figures_dir(run_dir) / MANIFEST_NAME


def read_manifest(run_dir: str | Path) -> FigureManifest | None:
    path = manifest_path(run_dir)
    if not path.is_file():
        return None
    try:
        return FigureManifest.model_validate(read_json(path))
    except Exception as exc:  # noqa: BLE001 - a damaged manifest means regenerate
        logger.warning("could not read figure manifest %s: %s", path, exc)
        return None


def clear_figures(run_dir: str | Path, *, remove_images: bool = True) -> None:
    """Remove the derived manifest (and optionally rendered images)."""
    directory = figures_dir(run_dir)
    for name in (MANIFEST_NAME,):
        try:
            (directory / name).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("could not remove stale %s", name, exc_info=True)
    if remove_images:
        for pattern in ("*.png", "*.jpg", "*.jpeg"):
            for path in directory.glob(pattern):
                try:
                    path.unlink()
                except OSError:
                    logger.warning("could not remove stale figure %s", path, exc_info=True)


def _caption(number: int, title: str) -> str:
    return f"图 {number} {title}"


def _source_hash(
    *,
    kind: str,
    title: str,
    body: str,
    data: dict[str, Any],
    width_px: int,
) -> str:
    payload = {
        "kind": kind,
        "title": title,
        "data": data,
        "body": body,
        "width_px": width_px,
        "version": FIGURE_VERSION,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _safe_image_path(run_dir: Path, file: str) -> Path:
    figures = figures_dir(run_dir).resolve()
    target = (run_dir / file).resolve()
    if not target.is_relative_to(figures):
        raise ValueError("figure path escapes the figures directory")
    return target


def _new_file(order: int, kind: str, source_hash: str) -> str:
    return f"figures/fig-{order:02d}-{kind}-{source_hash[:8]}.png"


def _image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        image.verify()
    with Image.open(path) as image:
        return int(image.width), int(image.height)


def _reuse_existing(
    existing: FigureManifest | None,
    *,
    section_id: str,
    kind: str,
    source_hash: str,
    run_dir: Path,
) -> FigureSpec | None:
    if existing is None:
        return None
    for spec in existing.figures:
        if spec.section_id != section_id or spec.kind != kind:
            continue
        if spec.source_hash != source_hash:
            continue
        try:
            path = _safe_image_path(run_dir, spec.file)
        except ValueError:
            return None
        if not path.is_file() or path.stat().st_size <= 0:
            return None
        return spec.model_copy(deep=True)
    return None


def _plan_is_usable(outputs: dict[str, SectionOutput] | None, section: OutlineSection) -> bool:
    if section.kind != "narrative":
        return True
    if outputs is None:
        return True
    output = outputs.get(section.id)
    return bool(
        output is not None
        and output.status == "final"
        and (output.final_markdown or "").strip()
    )


def _write_manifest(run_dir: Path, manifest: FigureManifest) -> None:
    ensure_dir(figures_dir(run_dir))
    atomic_write_json(manifest_path(run_dir), manifest.model_dump(mode="json"))


def _prune_orphans(run_dir: Path, manifest: FigureManifest) -> None:
    directory = figures_dir(run_dir)
    if not directory.is_dir():
        return
    referenced = {
        str(_safe_image_path(run_dir, spec.file).resolve())
        for spec in manifest.figures
        if _safe_image_path(run_dir, spec.file).is_relative_to(directory.resolve())
    }
    for pattern in ("*.png", "*.jpg", "*.jpeg"):
        for path in directory.glob(pattern):
            try:
                if str(path.resolve()) not in referenced:
                    path.unlink()
            except OSError:
                logger.warning("could not prune orphan figure %s", path, exc_info=True)


def generate_figures(
    *,
    context: TenderContext,
    profile: BidderProfile,
    outline: BidOutline,
    section_markdowns: dict[str, str],
    outputs: dict[str, SectionOutput] | None,
    run_dir: str | Path,
    settings: BidSettings,
    llm: ChatClient | None = None,
    external_renderer: ExternalRenderer | None = None,
    force: bool = False,
) -> FigureManifest:
    """Generate/reuse figures. Never raises for a single figure failure."""
    root = Path(run_dir)
    enabled = bool(getattr(settings, "figures_enabled", True))
    existing = None if force else read_manifest(root)
    manifest = FigureManifest(
        version=FIGURE_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        enabled=enabled,
    )
    if not enabled:
        manifest.stats = {
            "planned": 0,
            "generated": 0,
            "skipped": 0,
            "failed": 0,
            "reused": 0,
            "external": 0,
            "page_estimate": 0.0,
        }
        _prune_orphans(root, manifest)
        _write_manifest(root, manifest)
        return manifest

    max_figures = int(getattr(settings, "figures_max", 4) or 0)
    applicable = select_rules(outline, max_figures=max_figures)
    planner = FigurePlanner(
        settings,
        llm=llm if getattr(settings, "figures_use_llm", True) else None,
    )
    use_external = bool(
        external_renderer is not None and getattr(settings, "figures_external", False)
    )
    fonts = getattr(settings, "figure_font", "") or None
    width_px = int(getattr(settings, "figure_width_px", 1800) or 1800)
    width_cm = float(getattr(settings, "figure_image_width_cm", 15.5) or 15.5)
    page_estimate = float(getattr(settings, "figure_page_estimate", 0.5) or 0.5)

    planned = 0
    reused = 0
    failed = 0
    skipped_no_data = 0

    for section, rule in applicable:
        planned += 1
        body = section_markdowns.get(section.id, "").strip()
        if not _plan_is_usable(outputs, section) or not body:
            skipped_no_data += 1
            manifest.skipped.append(
                {
                    "section_id": section.id,
                    "section_title": section.title,
                    "kind": rule.kind,
                    "reason": "章节尚未生成或为空",
                }
            )
            continue
        try:
            plan = planner.plan(
                section=section,
                rule=rule,
                body=body,
                context=context,
                profile=profile,
            )
        except Exception as exc:  # noqa: BLE001 - optional figure
            logger.warning("figure planning failed for %s: %s", section.id, exc)
            failed += 1
            manifest.skipped.append(
                {
                    "section_id": section.id,
                    "section_title": section.title,
                    "kind": rule.kind,
                    "reason": f"规划失败：{type(exc).__name__}",
                }
            )
            continue
        if plan is None:
            skipped_no_data += 1
            manifest.skipped.append(
                {
                    "section_id": section.id,
                    "section_title": section.title,
                    "kind": rule.kind,
                    "reason": "现有资料不足，未生成该图",
                }
            )
            continue

        source_hash = _source_hash(
            kind=rule.kind,
            title=plan.title,
            body=body,
            data=plan.data,
            width_px=width_px,
        )
        manifest.source_hashes[section.id] = source_hash
        spec = _reuse_existing(
            existing,
            section_id=section.id,
            kind=rule.kind,
            source_hash=source_hash,
            run_dir=root,
        )
        if spec is not None:
            spec.warnings = list(dict.fromkeys([*spec.warnings, *plan.warnings]))
            spec.title = plan.title
            spec.data = plan.data
            reused += 1
            manifest.figures.append(spec)
            continue

        order = len(manifest.figures) + 1
        file = _new_file(order, rule.kind, source_hash)
        target = _safe_image_path(root, file)
        external = False
        warnings = list(dict.fromkeys(plan.warnings))
        try:
            rendered = False
            if use_external and rule.kind in _EXTERNAL_KINDS and external_renderer is not None:
                try:
                    rendered = bool(external_renderer(plan.data, target))
                except Exception as exc:  # noqa: BLE001 - fallback to internal
                    warnings.append(f"外部绘图工具失败，已回退内置渲染：{type(exc).__name__}")
                    logger.warning("external figure renderer failed: %s", exc)
                    rendered = False
                if rendered:
                    external = True
                    warnings.append("由外部绘图工具生成，须人工核验")
            if not rendered:
                result = render_figure(
                    kind=rule.kind,
                    data=plan.data,
                    title=plan.title,
                    disclaimer=rule.disclaimer,
                    output_path=target,
                    width_px=width_px,
                    font_path=fonts,
                )
                image_width, image_height = result.width, result.height
            else:
                image_width, image_height = _image_size(target)
                if image_width > 4000 or image_height > 4000:
                    raise FigureRenderError("external image dimensions are too large")
        except Exception as exc:  # noqa: BLE001 - one failed figure must not block assembly
            logger.warning("figure render failed for %s: %s", section.id, exc)
            failed += 1
            try:
                target.unlink()
            except OSError:
                pass
            manifest.skipped.append(
                {
                    "section_id": section.id,
                    "section_title": section.title,
                    "kind": rule.kind,
                    "reason": f"渲染失败：{str(exc)[:180]}",
                    "warnings": warnings,
                }
            )
            continue

        disclaimer = rule.disclaimer
        if external:
            disclaimer = f"{disclaimer}（由外部绘图工具生成，须人工核验）"
        spec = FigureSpec(
            figure_id=f"fig-{order:02d}-{rule.kind}",
            section_id=section.id,
            section_title=section.title,
            kind=rule.kind,
            title=plan.title,
            caption=_caption(order, plan.title),
            disclaimer=disclaimer,
            source="mcp" if external else plan.source,  # type: ignore[arg-type]
            file=file,
            data=plan.data,
            source_hash=source_hash,
            width_cm=width_cm,
            image_width=image_width,
            image_height=image_height,
            page_estimate=page_estimate,
            warnings=warnings,
            external=external,
        )
        manifest.figures.append(spec)

    # Renumber captions after skips/reuse so document numbering is contiguous.
    for index, spec in enumerate(manifest.figures, start=1):
        spec.figure_id = f"fig-{index:02d}-{spec.kind}"
        spec.caption = _caption(index, spec.title)
        spec.width_cm = width_cm
        spec.page_estimate = page_estimate

    external_count = sum(1 for spec in manifest.figures if spec.external)
    manifest.stats = {
        "planned": planned,
        "generated": len(manifest.figures),
        "skipped": skipped_no_data,
        "failed": failed,
        "reused": reused,
        "external": external_count,
        "page_estimate": round(len(manifest.figures) * page_estimate, 2),
    }
    if failed:
        manifest.warnings.append(f"{failed} 张配图生成失败，请人工补充")
    if skipped_no_data:
        manifest.warnings.append(f"{skipped_no_data} 张配图因资料不足跳过")
    manifest.warnings = list(dict.fromkeys(manifest.warnings))
    _prune_orphans(root, manifest)
    _write_manifest(root, manifest)
    return manifest


def build_figure_blocks(manifest: FigureManifest | None) -> list[FigureBlock]:
    if manifest is None or not manifest.enabled:
        return []
    replace_by_section = {
        rule.section_id: rule.replace_text
        for rule in FIGURE_RULES
        if rule.replace_text
    }
    blocks: list[FigureBlock] = []
    for spec in manifest.figures:
        block = (
            f"![{spec.caption}]({spec.file})\n\n"
            f"*{spec.caption}*\n\n"
            f"*注：{spec.disclaimer}*"
        )
        blocks.append(
            FigureBlock(
                section_id=spec.section_id,
                markdown=block,
                replace_text=replace_by_section.get(spec.section_id),
            )
        )
    return blocks


def figure_report(manifest: FigureManifest | None) -> dict[str, Any]:
    if manifest is None:
        return {
            "enabled": False,
            "stats": {},
            "figures": [],
            "warnings": [],
        }
    return {
        "enabled": manifest.enabled,
        "stats": dict(manifest.stats),
        "figures": [
            {
                "figure_id": spec.figure_id,
                "caption": spec.caption,
                "kind": spec.kind,
                "section_id": spec.section_id,
                "file": spec.file,
                "external": spec.external,
            }
            for spec in manifest.figures
        ],
        "skipped": list(manifest.skipped),
        "warnings": list(manifest.warnings),
    }


__all__ = [
    "ExternalRenderer",
    "build_figure_blocks",
    "clear_figures",
    "figure_report",
    "figures_dir",
    "generate_figures",
    "manifest_path",
    "read_manifest",
]
