"""Render a stored DOCX template as PNG pages for the template library."""

from __future__ import annotations

MAX_PREVIEW_PDF_BYTES = 100 * 1024 * 1024
PREVIEW_DPI = 110
MIN_PREVIEW_DPI = 36
MAX_PREVIEW_DPI = 200


class TemplatePreviewError(ValueError):
    """Raised when a template preview PDF cannot be rendered."""


def render_pdf_page_png(
    pdf_bytes: bytes,
    page: int,
    *,
    dpi: int = PREVIEW_DPI,
) -> tuple[bytes, int]:
    """Render one 1-based page of a PDF to PNG and return (png, page_count)."""

    if page < 1:
        raise TemplatePreviewError("page must be greater than or equal to 1")
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - dependency is installed
        raise TemplatePreviewError("PyMuPDF is not available") from exc
    try:
        document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - PyMuPDF error text is useful
        raise TemplatePreviewError(
            f"could not read the template preview PDF: {exc}"
        ) from exc
    try:
        pages = int(document.page_count)
        if pages <= 0:
            raise TemplatePreviewError("the template preview PDF has no pages")
        if page > pages:
            raise TemplatePreviewError(
                f"page {page} is out of range; the template has {pages} pages"
            )
        zoom_dpi = max(MIN_PREVIEW_DPI, min(MAX_PREVIEW_DPI, int(dpi)))
        pixmap = document.load_page(page - 1).get_pixmap(dpi=zoom_dpi)
        return pixmap.tobytes("png"), pages
    except TemplatePreviewError:
        raise
    except Exception as exc:  # noqa: BLE001 - PyMuPDF error text is useful
        raise TemplatePreviewError(
            f"could not render the template preview: {exc}"
        ) from exc
    finally:
        document.close()


__all__ = [
    "MAX_PREVIEW_PDF_BYTES",
    "PREVIEW_DPI",
    "TemplatePreviewError",
    "render_pdf_page_png",
]
