"""MinerU parser: upload, poll, download and normalize into canonical blocks."""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import Callable

import pymupdf

from ...assemble import assemble_document
from ...config import Settings
from ...errors import MineruError, MineruErrorKind
from ...models import ContentBlock, ParsedDocument
from ...toc import build_toc, normalize_title
from ..footer import clean_text, strip_hash
from .client import BatchFileResult, MineruClient, UploadSpec
from .split import PdfPart, split_pdf
from .zip_utils import locate_artifacts, safe_extract

logger = logging.getLogger(__name__)

_BLOCK_TYPES = {"text", "title", "table", "image", "equation", "list"}
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_SAFE_DATA_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _data_id(doc_id: str, index: int) -> str:
    safe = _SAFE_DATA_ID_RE.sub("-", doc_id).strip("-")[:100] or "doc"
    return f"{safe}-{index}"


def _pages_per_part(total_pages: int, max_pages: int, split_pages: int, *, force_split: bool = False) -> int:
    """Choose a balanced part size that stays under both configured limits."""
    import math

    needed = max(1, math.ceil(total_pages / max(1, max_pages)))
    if force_split:
        needed = max(2, needed)
    balanced = max(1, math.ceil(total_pages / needed))
    return max(1, min(balanced, max(1, split_pages)))


def _failed_state_error(result: BatchFileResult) -> MineruError:
    message = (result.err_msg or result.state or "").strip()
    lowered = message.lower()
    if "page" in lowered and ("limit" in lowered or "exceed" in lowered):
        return MineruError(MineruErrorKind.PAGE_LIMIT, message)
    return MineruError(
        MineruErrorKind.OUTPUT,
        f"MinerU failed for {result.file_name or 'file'}: {message or result.state}",
    )


def _caption(value: object) -> str:
    if isinstance(value, list):
        return " ".join(str(item) for item in value if item)
    return str(value or "")


def _item_to_block(item: dict, index: int, offset: int) -> ContentBlock | None:
    kind = str(item.get("type") or "text").lower()
    if kind not in _BLOCK_TYPES:
        kind = "text"
    page = int(item.get("page_idx") or 0) + 1 + offset
    text = str(item.get("text") or item.get("content") or "")
    table_body = item.get("table_body") or item.get("html")
    if table_body:
        kind = "table"
    caption = strip_hash(
        _caption(item.get("table_caption") or item.get("img_caption") or item.get("caption"))
    )
    img_path = item.get("img_path")
    level = int(item.get("text_level") or item.get("level") or 0)
    if kind in ("text", "title", "list"):
        text = clean_text(text)
    else:
        text = strip_hash(text).strip()
    if table_body:
        table_body = strip_hash(str(table_body)).strip()
    if not text and not table_body and not img_path:
        return None
    return ContentBlock(
        page=max(1, page),
        order=index,
        type=kind,
        text=text,
        level=level,
        table_body=str(table_body).strip() if table_body else None,
        img_path=str(img_path) if img_path else None,
        caption=caption.strip() or None,
    )


def _blocks_from_markdown(
    markdown: str,
    toc: list,
    part: PdfPart,
) -> list[ContentBlock]:
    blocks: list[ContentBlock] = []
    buffer: list[str] = []
    current_page = part.start_page
    order = 0

    def flush() -> None:
        nonlocal order
        if not buffer:
            return
        text = clean_text("\n".join(buffer))
        buffer.clear()
        if text:
            blocks.append(ContentBlock(page=current_page, order=order, type="text", text=text))
            order += 1

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        heading = _HEADING_RE.match(line)
        if heading:
            flush()
            title = heading.group(2).strip()
            norm = normalize_title(title)
            matches = [entry for entry in toc if entry.title_norm == norm]
            if matches:
                best = min(matches, key=lambda entry: abs(entry.page - current_page))
                current_page = best.page
            if title:
                blocks.append(
                    ContentBlock(
                        page=current_page,
                        order=order,
                        type="title",
                        text=title,
                        level=len(heading.group(1)),
                    )
                )
                order += 1
        elif not line:
            flush()
        else:
            buffer.append(raw_line)
    flush()
    return blocks


class MineruParser:
    name = "mineru"

    def __init__(self, settings: Settings, client: MineruClient | None = None) -> None:
        self.settings = settings
        self.client = client

    def parse(
        self,
        pdf_path: Path,
        doc_id: str,
        *,
        limit_pages: int | None = None,
        raw_dir: Path | None = None,
        progress: Callable[[str, int, str], None] | None = None,
    ) -> ParsedDocument:
        if not self.settings.mineru_api_key:
            raise MineruError(MineruErrorKind.AUTH, "MINERU_API_KEY is not configured")

        pdf_path = Path(pdf_path)
        size_mb = pdf_path.stat().st_size / (1024 * 1024)
        if size_mb > self.settings.mineru_max_file_mb:
            raise MineruError(
                MineruErrorKind.OUTPUT,
                f"file is {size_mb:.1f}MB, above the {self.settings.mineru_max_file_mb}MB MinerU limit",
            )

        source = pymupdf.open(str(pdf_path))
        try:
            total_pages = source.page_count
            raw_toc = source.get_toc(simple=True)
        finally:
            source.close()
        effective_pages = min(total_pages, limit_pages) if limit_pages else total_pages
        toc = build_toc(raw_toc, effective_pages, limit_pages)

        with tempfile.TemporaryDirectory(prefix="much-agents-mineru-") as temp_name:
            temp_dir = Path(temp_name)
            if effective_pages > self.settings.mineru_max_pages_per_file:
                pages_per_part = _pages_per_part(
                    effective_pages,
                    self.settings.mineru_max_pages_per_file,
                    self.settings.mineru_split_pages,
                )
                parts = split_pdf(
                    pdf_path,
                    temp_dir / "parts",
                    pages_per_part,
                    effective_pages,
                )
            else:
                parts = [PdfPart(path=pdf_path, start_page=1, end_page=effective_pages)]

            try:
                return self._run_parts(
                    parts, pdf_path, doc_id, toc, effective_pages, raw_dir, progress, temp_dir
                )
            except MineruError as exc:
                if exc.kind is MineruErrorKind.PAGE_LIMIT and len(parts) == 1 and effective_pages > 1:
                    logger.warning("MinerU rejected the page count; retrying with split parts")
                    pages_per_part = _pages_per_part(
                        effective_pages,
                        self.settings.mineru_max_pages_per_file,
                        self.settings.mineru_split_pages,
                        force_split=True,
                    )
                    parts = split_pdf(
                        pdf_path,
                        temp_dir / "parts_retry",
                        pages_per_part,
                        effective_pages,
                    )
                    return self._run_parts(
                        parts, pdf_path, doc_id, toc, effective_pages, raw_dir, progress, temp_dir
                    )
                raise

    def _run_parts(
        self,
        parts: list[PdfPart],
        pdf_path: Path,
        doc_id: str,
        toc: list,
        page_count: int,
        raw_dir: Path | None,
        progress: Callable[[str, int, str], None] | None,
        temp_dir: Path,
    ) -> ParsedDocument:
        owns_client = self.client is None
        client = self.client or MineruClient(self.settings)
        try:
            specs = [
                UploadSpec(
                    name=part.path.name,
                    data_id=_data_id(doc_id, index),
                    path=part.path,
                )
                for index, part in enumerate(parts)
            ]
            info = client.create_batch(specs)
            for spec, url in zip(specs, info.file_urls):
                if progress:
                    progress("mineru", 5, f"uploading {spec.name}")
                client.upload_file(url, spec.path)
            results = client.poll_batch(info.batch_id, progress)
            ordered = _order_results(results, specs)

            all_blocks: list[ContentBlock] = []
            warnings: list[str] = []
            for index, (part, result) in enumerate(zip(parts, ordered)):
                if result.state != "done" or not result.full_zip_url:
                    raise _failed_state_error(result)
                zip_path = client.download_zip(result.full_zip_url, temp_dir / f"zip_{index:03d}")
                extract_root = temp_dir / f"extract_{index:03d}"
                safe_extract(zip_path, extract_root)
                full_md, content_list, layout, images = locate_artifacts(extract_root)
                if full_md is None and content_list is None:
                    raise MineruError(
                        MineruErrorKind.OUTPUT,
                        f"MinerU result for {result.file_name or part.path.name} has no Markdown",
                    )
                self._copy_raw(raw_dir, index, len(parts), full_md, content_list, layout, images)

                raw_markdown = ""
                if full_md is not None:
                    raw_markdown = full_md.read_text(encoding="utf-8", errors="replace")
                part_blocks = self._load_content_list(content_list, index, part)
                if not part_blocks:
                    part_blocks = _blocks_from_markdown(raw_markdown, toc, part)
                    warnings.append(
                        f"content_list missing for {part.path.name}; aligned Markdown headings instead"
                    )
                elif raw_markdown:
                    block_chars = sum(
                        len(block.text) + len(block.table_body or "") for block in part_blocks
                    )
                    if block_chars < len(raw_markdown) * 0.6:
                        warnings.append(
                            f"content_list only covers {block_chars}/{len(raw_markdown)} chars "
                            f"for {part.path.name}; alignment may be incomplete"
                        )
                all_blocks.extend(part_blocks)

            all_blocks.sort(key=lambda block: (block.page, block.order))
            for order, block in enumerate(all_blocks):
                block.order = order
                block.page = max(1, min(page_count, block.page))
            if not all_blocks:
                raise MineruError(MineruErrorKind.OUTPUT, "MinerU returned no content blocks")

            markdown, sections, assembly_warnings = assemble_document(
                all_blocks, toc, doc_id, page_count
            )
            warnings.extend(assembly_warnings)
            return ParsedDocument(
                doc_id=doc_id,
                source_path=Path(pdf_path),
                markdown=markdown,
                content_list=all_blocks,
                toc=toc,
                sections=sections,
                page_count=page_count,
                parser_used="mineru",
                warnings=warnings,
                meta={
                    "batch_id": info.batch_id,
                    "parts": len(parts),
                    "raw_dir": str(raw_dir) if raw_dir else None,
                },
            )
        finally:
            if owns_client:
                client.close()

    def _load_content_list(
        self,
        content_list: Path | None,
        part_index: int,
        part: PdfPart,
    ) -> list[ContentBlock]:
        if content_list is None or not Path(content_list).is_file():
            return []
        try:
            payload = json.loads(Path(content_list).read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            return []
        if isinstance(payload, dict):
            for key in ("content_list", "content", "data"):
                if isinstance(payload.get(key), list):
                    payload = payload[key]
                    break
        if not isinstance(payload, list):
            return []
        offset = part.start_page - 1
        blocks: list[ContentBlock] = []
        for index, item in enumerate(payload):
            if not isinstance(item, dict):
                continue
            block = _item_to_block(item, index, offset)
            if block is not None:
                blocks.append(block)
        return blocks

    def _copy_raw(
        self,
        raw_dir: Path | None,
        index: int,
        total: int,
        full_md: Path | None,
        content_list: Path | None,
        layout: Path | None,
        images: Path | None,
    ) -> None:
        if raw_dir is None:
            return
        raw_dir = Path(raw_dir)
        raw_dir.mkdir(parents=True, exist_ok=True)
        prefix = f"part_{index + 1:03d}_" if total > 1 else ""
        for source, name in (
            (full_md, f"{prefix}full.md"),
            (content_list, f"{prefix}content_list.json"),
            (layout, f"{prefix}layout.json"),
        ):
            if source is not None and Path(source).is_file():
                shutil.copy2(source, raw_dir / name)
        if images is not None and Path(images).is_dir():
            target = raw_dir / "images"
            target.mkdir(parents=True, exist_ok=True)
            for item in Path(images).iterdir():
                if item.is_file():
                    shutil.copy2(item, target / item.name)


def _order_results(
    results: list[BatchFileResult],
    specs: list[UploadSpec],
) -> list[BatchFileResult]:
    by_data = {item.data_id: item for item in results if item.data_id}
    by_name = {item.file_name: item for item in results if item.file_name}
    ordered: list[BatchFileResult] = []
    for spec in specs:
        found = by_data.get(spec.data_id) or by_name.get(spec.name)
        if found is None:
            raise MineruError(
                MineruErrorKind.OUTPUT,
                f"MinerU did not return a result for {spec.name}",
            )
        ordered.append(found)
    return ordered
