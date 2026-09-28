"""Text extraction service for document ingestion.

Supports: PDF, DOCX, PPTX, XLSX, CSV, TXT, MD, HTML.
Uses CPU-only libraries already installed in the venv.

Each extractor returns a list of PageContent blocks so that page_ref,
section_ref, and row_ref can be propagated to DocumentChunk records.
"""
from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)

# File-type → human-readable extraction method name
_TXT_MIME = "text/plain"
_MD_MIME = "text/markdown"


@dataclass
class PageContent:
    """A chunk of extracted text with source location metadata."""

    content: str
    page_ref: int | None = None
    section_ref: str | None = None
    row_ref: int | None = None


@dataclass
class ExtractionResult:
    """Result of text extraction from a single document."""

    pages: list[PageContent] = field(default_factory=list)
    extraction_method: str = "unknown"
    total_chars: int = 0
    total_pages: int = 0


class ExtractionError(Exception):
    """Raised when text extraction fails for a document."""


def _detect_encoding(raw: bytes) -> str:
    """Detect the encoding of raw bytes, falling back to utf-8."""
    try:
        import chardet
        detected = chardet.detect(raw)
        return detected["encoding"] or "utf-8"
    except Exception:
        return "utf-8"


class DocumentExtractor:
    """Extracts text from uploaded documents into structured PageContent blocks.

    Each file-type handler returns a list of PageContent, preserving page,
    section, and row references where applicable.
    """

    # Map of (mime_type, extension) → handler method name
    SUPPORTED: dict[str, str] = {
        "application/pdf": "_extract_pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "_extract_docx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": "_extract_pptx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "_extract_xlsx",
        "text/csv": "_extract_csv",
        "text/plain": "_extract_txt",
        "text/markdown": "_extract_txt",
        "text/html": "_extract_html",
        "application/xhtml+xml": "_extract_html",
    }

    SUPPORTED_EXTENSIONS: set[str] = {
        ".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".txt", ".md", ".html",
    }

    def _get_handler(self, mime_type: str | None, filename: str) -> Callable:
        """Return the extraction handler for a given mime/type or extension."""
        # Try mime_type first
        handler_name = self.SUPPORTED.get(mime_type) if mime_type else None
        if handler_name:
            return getattr(self, handler_name)

        # Fall back to extension
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        # Try both the extension and a mime-type guess
        if ext in self.SUPPORTED_EXTENSIONS:
            # Map extension to mime
            ext_to_mime = {
                ".pdf": "application/pdf",
                ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ".csv": "text/csv",
                ".txt": "text/plain",
                ".md": "text/markdown",
                ".html": "text/html",
            }
            handler_name = self.SUPPORTED.get(ext_to_mime.get(ext, ""))
            if handler_name:
                return getattr(self, handler_name)

        raise ExtractionError(
            f"Unsupported file type: mime={mime_type}, ext={ext}, filename={filename}"
        )

    def extract(self, content: bytes, mime_type: str | None, filename: str) -> ExtractionResult:
        """Extract text from file content bytes.

        Args:
            content: Raw file bytes.
            mime_type: MIME type from the upload (may be None).
            filename: Original filename for extension fallback.

        Returns:
            ExtractionResult with structured page content.
        """
        handler = self._get_handler(mime_type, filename)
        result = handler(io.BytesIO(content))

        # Compute total chars for quick summary
        result.total_chars = sum(len(page.content) for page in result.pages)
        result.total_pages = len(result.pages)
        return result

    # ── Individual extractors ──────────────────────────────────────

    def _extract_pdf(self, file_obj: io.BytesIO) -> ExtractionResult:
        """Extract text from PDF using pypdf (succeedsor to PyPDF2)."""
        try:
            from pypdf import PdfReader
        except ImportError:
            from PyPDF2 import PdfReader  # noqa: F401

        reader = PdfReader(file_obj)
        pages: list[PageContent] = []
        for i, page in enumerate(reader.pages):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            if text.strip():
                pages.append(PageContent(
                    content=text,
                    page_ref=i + 1,
                ))
        if not pages:
            pages.append(PageContent(content="", page_ref=1))
        return ExtractionResult(
            pages=pages,
            extraction_method="pypdf",
        )

    def _extract_docx(self, file_obj: io.BytesIO) -> ExtractionResult:
        """Extract text from DOCX using python-docx."""
        from docx import Document as DocxDocument

        doc = DocxDocument(file_obj)
        # Track sections
        sections: list[PageContent] = []
        current_section: list[str] = []
        current_section_name = "document"

        for para in doc.paragraphs:
            if para.text.strip():
                current_section.append(para.text)
            # Detect section-like breaks (headings)
            if para.style and "heading" in para.style.name.lower():
                if current_section:
                    sections.append(PageContent(
                        content="\n".join(current_section),
                        section_ref=current_section_name,
                    ))
                current_section_name = para.text
                current_section = []

        if current_section:
            sections.append(PageContent(
                content="\n".join(current_section),
                section_ref=current_section_name,
            ))

        if not sections:
            sections.append(PageContent(content=""))

        return ExtractionResult(pages=sections, extraction_method="python-docx")

    def _extract_pptx(self, file_obj: io.BytesIO) -> ExtractionResult:
        """Extract text from PPTX using python-pptx."""
        from pptx import Presentation

        prs = Presentation(file_obj)
        pages: list[PageContent] = []
        for slide_num, slide in enumerate(prs.slides, 1):
            texts: list[str] = []
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    texts.append(shape.text.strip())
            content = "\n".join(texts)
            if content.strip():
                pages.append(PageContent(
                    content=content,
                    page_ref=slide_num,
                ))
        if not pages:
            pages.append(PageContent(content="", page_ref=1))
        return ExtractionResult(pages=pages, extraction_method="python-pptx")

    def _extract_xlsx(self, file_obj: io.BytesIO) -> ExtractionResult:
        """Extract text from XLSX using openpyxl."""
        from openpyxl import load_workbook

        wb = load_workbook(file_obj, data_only=True, read_only=True)
        pages: list[PageContent] = []
        for ws in wb.worksheets:
            rows: list[str] = []
            for row_idx, row in enumerate(ws.iter_rows(values_only=True), 1):
                cell_values = [str(cell) for cell in row if cell is not None and str(cell).strip()]
                if cell_values:
                    line = "\t".join(cell_values)
                    rows.append(line)
            if rows:
                pages.append(PageContent(
                    content="\n".join(rows),
                    section_ref=ws.title,
                ))
        wb.close()
        if not pages:
            pages.append(PageContent(content=""))
        return ExtractionResult(pages=pages, extraction_method="openpyxl")

    def _extract_csv(self, file_obj: io.BytesIO) -> ExtractionResult:
        """Extract text from CSV using stdlib csv module."""
        raw = file_obj.read()
        encoding = _detect_encoding(raw)
        text_content = raw.decode(encoding, errors="replace")

        reader = csv.reader(io.StringIO(text_content))
        rows: list[str] = []
        for row in reader:
            if row:
                rows.append("\t".join(cell.strip() for cell in row))

        content = "\n".join(rows) if rows else ""
        return ExtractionResult(
            pages=[PageContent(content=content, row_ref=1)],
            extraction_method="csv",
        )

    def _extract_txt(self, file_obj: io.BytesIO) -> ExtractionResult:
        """Extract text from plain text or markdown."""
        raw = file_obj.read()
        encoding = _detect_encoding(raw)
        text = raw.decode(encoding, errors="replace")
        return ExtractionResult(
            pages=[PageContent(content=text)],
            extraction_method="utf-8-decode",
        )

    def _extract_html(self, file_obj: io.BytesIO) -> ExtractionResult:
        """Extract text from HTML using BeautifulSoup + lxml."""
        from bs4 import BeautifulSoup

        raw = file_obj.read()
        encoding = _detect_encoding(raw)
        html_text = raw.decode(encoding, errors="replace")

        soup = BeautifulSoup(html_text, "lxml")

        # Remove non-content elements
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
            tag.decompose()

        # Preserve some structure
        for br in soup.find_all("br"):
            br.replace_with("\n")
        for p in soup.find_all("p"):
            p.append("\n")

        text = soup.get_text(separator="\n", strip=True)

        return ExtractionResult(
            pages=[PageContent(content=text)],
            extraction_method="beautifulsoup4-lxml",
        )
