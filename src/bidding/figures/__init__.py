"""Deterministic bid illustration generation."""

from .schema import FigureBlock, FigureManifest, FigureSpec
from .service import (
    ExternalRenderer,
    append_manifest_warnings,
    build_figure_blocks,
    clear_figures,
    figure_report,
    generate_figures,
    read_manifest,
)

__all__ = [
    "ExternalRenderer",
    "FigureBlock",
    "FigureManifest",
    "FigureSpec",
    "append_manifest_warnings",
    "build_figure_blocks",
    "clear_figures",
    "figure_report",
    "generate_figures",
    "read_manifest",
]
