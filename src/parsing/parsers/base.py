"""Parser protocol shared by MinerU and PyMuPDF."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..models import ParsedDocument


class Parser(Protocol):
    name: str

    def parse(
        self,
        pdf_path: Path,
        doc_id: str,
        *,
        limit_pages: int | None = None,
        raw_dir: Path | None = None,
    ) -> ParsedDocument: ...
