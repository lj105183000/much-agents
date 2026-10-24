"""Deterministic bid illustration generation."""

from .schema import FigureBlock, FigureManifest, FigureSpec
from .service import (
    build_figure_blocks,
    clear_figures,
    figure_report,
    generate_figures,
    read_manifest,
)

__all__ = [
    "FigureBlock",
    "FigureManifest",
    "FigureSpec",
    "build_figure_blocks",
    "clear_figures",
    "figure_report",
    "generate_figures",
    "read_manifest",
]
