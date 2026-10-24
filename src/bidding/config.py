"""Configuration for bid-document generation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv

from src.parsing.config import Settings, find_project_root
from src.storage.settings import StorageSettings

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _str(env: Mapping[str, str], key: str, default: str) -> str:
    value = env.get(key)
    if value is None:
        return default
    value = str(value).strip()
    return value or default


def _int(env: Mapping[str, str], key: str, default: int, minimum: int = 0) -> int:
    raw = env.get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


def _float(env: Mapping[str, str], key: str, default: float, minimum: float = 0.0) -> float:
    raw = env.get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None or not str(raw).strip():
        return default
    value = str(raw).strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return default


@dataclass(frozen=True, slots=True)
class BidSettings:
    base: Settings
    bids_dir: Path
    max_sections: int = 0
    retrieval_top_k: int = 6
    max_evidence_chars: int = 6000
    max_tokens: int = 8192
    temperature: float = 0.2
    max_review_revisions: int = 1
    llm_concurrency: int = 2
    use_chroma: bool = True
    profile_max_mb: int = 5
    figures_enabled: bool = True
    figures_max: int = 4
    figures_use_llm: bool = True
    figure_font: str = ""
    figure_width_px: int = 1800
    figure_image_width_cm: float = 15.5
    figure_page_estimate: float = 0.5
    figures_external: bool = False

    @property
    def parsed_dir(self) -> Path:
        return self.base.parsed_dir

    @property
    def upload_dir(self) -> Path:
        return self.base.upload_dir

    @property
    def storage(self) -> StorageSettings:
        return self.base.storage

    @classmethod
    def from_env(
        cls,
        env_file: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> "BidSettings":
        base = Settings.from_env(env_file=env_file, env=env)
        if env is None:
            if env_file is None:
                env_file = base.project_root / ".env"
            if env_file is not None and Path(env_file).is_file():
                load_dotenv(env_file, override=False, encoding="utf-8")
            env = dict(os.environ)

        bids_dir = Path(_str(env, "BIDS_DIR", str(base.project_root / "upload" / "bids")))
        if not bids_dir.is_absolute():
            bids_dir = base.project_root / bids_dir

        return cls(
            base=base,
            bids_dir=bids_dir,
            max_sections=_int(env, "BID_MAX_SECTIONS", 0),
            retrieval_top_k=_int(env, "BID_RETRIEVAL_TOP_K", 6, minimum=1),
            max_evidence_chars=_int(env, "BID_MAX_EVIDENCE_CHARS", 6000, minimum=500),
            max_tokens=_int(env, "BID_MAX_TOKENS", 8192, minimum=512),
            temperature=_float(env, "BID_TEMPERATURE", 0.2, minimum=0.0),
            max_review_revisions=_int(env, "BID_MAX_REVIEW_REVISIONS", 1, minimum=0),
            llm_concurrency=_int(env, "BID_LLM_CONCURRENCY", 2, minimum=1),
            use_chroma=_bool(env, "BID_USE_CHROMA", True),
            profile_max_mb=_int(env, "BID_PROFILE_MAX_MB", 5, minimum=1),
            figures_enabled=_bool(env, "BID_FIGURES_ENABLED", True),
            figures_max=_int(env, "BID_FIGURES_MAX", 4, minimum=0),
            figures_use_llm=_bool(env, "BID_FIGURES_USE_LLM", True),
            figure_font=_str(env, "BID_FIGURES_FONT", ""),
            figure_width_px=_int(env, "BID_FIGURES_WIDTH_PX", 1800, minimum=800),
            figure_image_width_cm=_float(
                env, "BID_FIGURES_IMAGE_WIDTH_CM", 15.5, minimum=5.0
            ),
            figure_page_estimate=_float(
                env, "BID_FIGURES_PAGE_ESTIMATE", 0.5, minimum=0.0
            ),
            figures_external=_bool(env, "BID_FIGURES_EXTERNAL", False),
        )


__all__ = ["BidSettings", "find_project_root"]
