"""Text chunking service for document ingestion.

Implements a recursive character text splitter (analogous to LangChain's
RecursiveCharacterTextSplitter). Splits on semantic boundaries first, then
falls back to smaller separators. Configurable chunk_size and chunk_overlap.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.documents.extractor import PageContent

logger = logging.getLogger(__name__)


@dataclass
class Chunk:
    """A single text chunk with source location metadata."""

    content: str
    chunk_index: int
    source_page: int | None = None
    source_section: str | None = None
    source_row: int | None = None


class TextChunker:
    """Recursive character text splitter with configurable chunk size and overlap.

    Args:
        chunk_size: Maximum characters per chunk (default 512).
        chunk_overlap: Number of overlapping characters between adjacent chunks
            (default 128). Must be < chunk_size.

    The splitter tries separators in order of semantic significance:
    1. Paragraph breaks (\\n\\n)
    2. Line breaks (\\n)
    3. Sentence boundaries
    4. Word boundaries
    5. Character-level fallback
    """

    # Ordered list of separators, most to least significant
    DEFAULT_SEPARATORS: list[str] = [
        "\n\n",
        "\n",
        "。",
        ".",
        "，",
        ",",
        " ",
        "",
    ]

    def __init__(
        self,
        chunk_size: int = 512,
        chunk_overlap: int = 128,
        separators: list[str] | None = None,
    ):
        if chunk_overlap >= chunk_size:
            raise ValueError(
                f"chunk_overlap ({chunk_overlap}) must be < chunk_size ({chunk_size})"
            )
        if chunk_overlap < 0:
            raise ValueError(f"chunk_overlap must be >= 0, got {chunk_overlap}")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = separators or self.DEFAULT_SEPARATORS

    def chunk_text(self, text: str) -> list[str]:
        """Split a single text string into chunks.

        Args:
            text: The text to split.

        Returns:
            List of text chunks with overlap applied.
        """
        if not text or not text.strip():
            return []

        # Use the recursive split + overlap approach
        splits = self._split_text_recursive(text, self.separators)
        chunks: list[str] = []
        current_chunk: list[str] = []

        for split in splits:
            if sum(len(s) for s in current_chunk) + len(split) <= self.chunk_size:
                current_chunk.append(split)
            else:
                if current_chunk:
                    chunks.append(self._join_chunks(current_chunk))
                # If the split itself is larger than chunk_size, hard-split it
                if len(split) > self.chunk_size:
                    for i in range(0, len(split), self.chunk_size):
                        hard_chunk = split[i : i + self.chunk_size]
                        chunks.append(hard_chunk)
                else:
                    current_chunk = [split]
        if current_chunk:
            chunks.append(self._join_chunks(current_chunk))

        # Apply sliding window overlap
        if self.chunk_overlap > 0 and len(chunks) > 1:
            chunks = self._apply_overlap(chunks)

        return [c for c in chunks if c.strip()]

    def chunk_pages(self, pages: list[PageContent]) -> list[Chunk]:
        """Chunk a list of PageContent blocks, preserving source references.

        Args:
            pages: List of PageContent blocks from the extractor.

        Returns:
            List of Chunk objects with chunk_index and source metadata.
        """
        all_chunks: list[Chunk] = []
        chunk_index = 0

        for page in pages:
            if not page.content.strip():
                continue
            page_chunks = self.chunk_text(page.content)
            for page_chunk in page_chunks:
                all_chunks.append(Chunk(
                    content=page_chunk,
                    chunk_index=chunk_index,
                    source_page=page.page_ref,
                    source_section=page.section_ref,
                    source_row=page.row_ref,
                ))
                chunk_index += 1

        return all_chunks

    def _split_text_recursive(self, text: str, separators: list[str]) -> list[str]:
        """Recursively split text using separators, smallest granularity last."""
        if not text:
            return []

        # Find the best separator (first one in the list that appears in the text)
        for i, sep in enumerate(separators):
            if sep == "" or not sep:
                # Empty separator → no further splitting possible;
                # return as single chunk (hard-split handles oversize)
                return [text]

            if sep in text:
                # Split on this separator
                parts = text.split(sep)
                # Recurse on each part with the remaining separators
                result: list[str] = []
                for part in parts:
                    if len(part) <= self.chunk_size:
                        result.append(part + sep if i > 0 else part)
                    else:
                        # Need to split further
                        sub_parts = self._split_text_recursive(part, separators[i + 1:])
                        result.extend(sub_parts)
                return result if result else [text]

        # No separator found in the list → return as single chunk
        return [text]

    def _join_chunks(self, parts: list[str]) -> str:
        """Join chunk parts with appropriate separators."""
        # Preserve newlines between parts
        return "\n".join(parts)

    def _apply_overlap(self, chunks: list[str]) -> list[str]:
        """Apply overlapping characters between adjacent chunks.

        For each chunk boundary, append the tail of the previous chunk
        to the beginning of the current chunk.
        """
        overlapped: list[str] = []
        prev_tail = ""

        for i, chunk in enumerate(chunks):
            if i == 0:
                overlapped.append(chunk)
                prev_tail = chunk[-self.chunk_overlap :] if len(chunk) >= self.chunk_overlap else chunk
            else:
                # Prepend overlap from previous chunk
                overlap = prev_tail[-self.chunk_overlap:] if prev_tail else ""
                overlapped_chunk = overlap + chunk if overlap else chunk
                overlapped.append(overlapped_chunk)
                # Update prev_tail
                prev_tail = chunk[-self.chunk_overlap :] if len(chunk) >= self.chunk_overlap else chunk

        return overlapped
