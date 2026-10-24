"""Typed figure metadata for automatically generated bid illustrations."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

FigureKind = Literal["org_chart", "process_flow", "gantt", "site_plan"]
FigureSource = Literal["profile", "tender", "section", "mcp"]


class FigureSpec(BaseModel):
    """One rendered (or reused) figure in a bid run."""

    model_config = ConfigDict(extra="forbid")

    figure_id: str
    section_id: str
    section_title: str
    kind: FigureKind
    title: str
    caption: str
    disclaimer: str
    source: FigureSource
    file: str
    data: dict[str, Any] = Field(default_factory=dict)
    source_hash: str
    width_cm: float = 15.5
    image_width: int = 0
    image_height: int = 0
    page_estimate: float = 0.5
    warnings: list[str] = Field(default_factory=list)
    external: bool = False

    @field_validator("file")
    @classmethod
    def _safe_figure_file(cls, value: str) -> str:
        text = (value or "").strip()
        if not text.startswith("figures/") or ".." in text or "\\" in text:
            raise ValueError("figure file must be a safe figures/ relative path")
        suffix = text.rsplit(".", 1)[-1].lower() if "." in text else ""
        if suffix not in {"png", "jpg", "jpeg"}:
            raise ValueError("figure file must be an image")
        return text


class FigureManifest(BaseModel):
    """On-disk figure manifest for one bid run (not persisted in PostgreSQL)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    version: str = "figures-v1"
    generated_at: str
    enabled: bool = True
    source_hashes: dict[str, str] = Field(default_factory=dict)
    figures: list[FigureSpec] = Field(default_factory=list)
    skipped: list[dict[str, Any]] = Field(default_factory=list)
    stats: dict[str, int | float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class FigureBlock(BaseModel):
    """A Markdown block inserted into one assembled section."""

    model_config = ConfigDict(extra="forbid")

    section_id: str
    markdown: str
    replace_text: str | None = None


__all__ = [
    "FigureBlock",
    "FigureKind",
    "FigureManifest",
    "FigureSource",
    "FigureSpec",
]
