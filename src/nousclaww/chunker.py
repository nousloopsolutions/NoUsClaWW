"""Deterministic text chunker with page/paragraph/line lineage.

Splits extracted text into chunks with deterministic boundaries and records
the page/paragraph/line lineage for each chunk. The chunk algorithm and
version are recorded so re-processing with a different algorithm creates a
new revision.

Contract:
    - Same input text + same algorithm version = same chunk boundaries
    - Every chunk records its source locator: page, paragraph, line range
    - Chunks do not cross page boundaries (page is the primary locator)
    - Chunk size is measured in characters, not tokens (no tokenizer dep)
    - Overlap is configurable and deterministic
    - Empty pages produce zero chunks (not empty chunks)

SYNTH:
    purpose: Deterministic character-based text chunker with page/paragraph/line lineage and configurable overlap
    axioms: [local_first, evidence_over_intuition, open_process, scientific_method]
    objective: Same input + same algorithm version always produces the same chunk boundaries; chunks never cross page boundaries; size is measured in characters not tokens; empty pages produce zero chunks
    anti_patterns:
        - Using a tokenizer for chunk sizing (character-based only, no dependency)
        - Allowing chunks to cross page boundaries
        - Producing empty chunks for empty pages (skip instead)
        - Non-deterministic chunking (same input must always produce same output)
        - Losing lineage information (every chunk must have page/paragraph/line)
"""
#C Adapted from NoUs-fordge Nous-hub mvp_local_core pipeline/chunker.py

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


# ── Chunk Dataclass ─────────────────────────────────────────────────────────

@dataclass
class Chunk:
    """A single text chunk with lineage information.

    Attributes:
        text: The chunk text content
        page: 1-based page number
        paragraph: 1-based paragraph within page
        line_start: 1-based line within page
        line_end: 1-based line within page
        char_offset: 0-based offset within the full document text
        char_count: Character count of this chunk
        algorithm_version: Chunk algorithm version string
        source_hash: Hash of the source text (for dedup detection)
    """

    text: str
    page: int
    paragraph: int
    line_start: int
    line_end: int
    char_offset: int
    char_count: int
    algorithm_version: str
    source_hash: str = ""

    def to_dict(self) -> dict:
        """Serialize to a dict for JSON output."""
        return {
            "text": self.text,
            "page": self.page,
            "paragraph": self.paragraph,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "char_offset": self.char_offset,
            "char_count": self.char_count,
            "algorithm_version": self.algorithm_version,
            "source_hash": self.source_hash,
        }


# ── Chunker ─────────────────────────────────────────────────────────────────

class Chunker:
    """Splits text into deterministic chunks with page/paragraph/line lineage.

    The chunker operates on text that may contain page markers in the format
    ``[Page N]`` (as produced by document extractors). For plain text files
    without page markers, the entire text is treated as a single page (page 1).

    Usage::

        chunker = Chunker()
        chunks = chunker.chunk(text, target_size=800, overlap=100)
        for c in chunks:
            print(f"Page {c.page}, para {c.paragraph}: {c.text[:50]}...")
    """

    ALGORITHM_NAME = "char_page_aware"
    ALGORITHM_VERSION = "1.0.0"

    def __init__(
        self,
        min_chunk_size: int = 10,
    ) -> None:
        """Initialize the chunker.

        Args:
            min_chunk_size: Minimum chunk size — chunks smaller than this
                           are merged with the previous chunk if possible.

        Raises:
            ValueError: If min_chunk_size is negative
        """
        if min_chunk_size < 0:
            raise ValueError("min_chunk_size cannot be negative")
        self.min_chunk_size = min_chunk_size

    def chunk(
        self,
        text: str,
        target_size: int = 800,
        overlap: int = 100,
        source_id: str = "",
    ) -> list[Chunk]:
        """Split text into deterministic chunks with lineage.

        Args:
            text: The full text to chunk. May contain ``[Page N]`` markers.
            target_size: Target chunk size in characters.
            overlap: Overlap between consecutive chunks in characters.
            source_id: Optional source identifier for chunk ID generation.

        Returns:
            List of Chunk objects with page/paragraph/line lineage.

        Raises:
            ValueError: If target_size <= 0, overlap < 0, or overlap >= target_size
        """
        if target_size <= 0:
            raise ValueError("target_size must be positive")
        if overlap < 0:
            raise ValueError("overlap cannot be negative")
        if overlap >= target_size:
            raise ValueError("overlap must be less than target_size")

        if not text or not text.strip():
            return []

        pages = self._split_pages(text)
        chunks: list[Chunk] = []
        global_char_offset = 0

        for page_num, page_text in pages:
            if not page_text.strip():
                # Empty page — skip, produces zero chunks
                # Account for the [Page N] marker in the offset
                marker = f"[Page {page_num}]\n"
                global_char_offset += len(marker)
                continue

            page_chunks = self._chunk_page(
                page_text, page_num, source_id,
                global_char_offset, target_size, overlap,
            )
            chunks.extend(page_chunks)
            # Account for the page marker that was in the original text
            marker = f"[Page {page_num}]\n"
            global_char_offset += len(marker) + len(page_text)

        # Merge tiny trailing chunks
        if self.min_chunk_size > 0 and len(chunks) >= 2:
            chunks = self._merge_tiny_chunks(chunks)

        return chunks

    # ── Page Splitting ─────────────────────────────────────────────────

    def _split_pages(self, text: str) -> list[tuple[int, str]]:
        """Split text into (page_num, page_text) tuples.

        Detects ``[Page N]`` markers. Text before the first marker is page 1.
        Text without any markers is a single page (page 1).

        Args:
            text: Full text potentially containing page markers

        Returns:
            List of (page_number, page_text) tuples
        """
        page_marker_re = re.compile(r"\[Page (\d+)\]\n?")
        markers = list(page_marker_re.finditer(text))

        if not markers:
            # No page markers — entire text is page 1
            return [(1, text)]

        pages: list[tuple[int, str]] = []
        # Text before first marker is page 1 (if any)
        pre_text = text[: markers[0].start()]
        if pre_text.strip():
            pages.append((1, pre_text.rstrip()))

        for i, match in enumerate(markers):
            page_num = int(match.group(1))
            start = match.end()
            end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
            page_text = text[start:end].rstrip()
            pages.append((page_num, page_text))

        return pages

    # ── Page Chunking ──────────────────────────────────────────────────

    def _chunk_page(
        self,
        page_text: str,
        page_num: int,
        source_id: str,
        base_offset: int,
        target_size: int,
        overlap: int,
    ) -> list[Chunk]:
        """Chunk a single page's text.

        Args:
            page_text: Text content of the page
            page_num: 1-based page number
            source_id: Source identifier for chunk ID generation
            base_offset: Character offset of this page in the full document
            target_size: Target chunk size in characters
            overlap: Overlap between chunks in characters

        Returns:
            List of Chunk objects for this page
        """
        # Split into paragraphs (double newline)
        paragraphs = re.split(r"\n\s*\n", page_text)

        chunks: list[Chunk] = []
        current_text = ""
        current_para = 0
        current_line_start = 1
        para_offset = 0

        for para_idx, para_text in enumerate(paragraphs, 1):
            if not para_text.strip():
                para_offset += len(para_text) + 2  # +2 for the split delimiter
                continue

            # If paragraph fits in a chunk and current buffer is small, append
            if len(current_text) + len(para_text) <= target_size:
                if current_text:
                    current_text += "\n\n" + para_text
                else:
                    current_text = para_text
                    current_para = para_idx
                    current_line_start = self._count_lines_before(
                        page_text, para_offset
                    )
                para_offset += len(para_text) + 2
                continue

            # Current buffer is full — emit chunk(s)
            if current_text and len(current_text) >= self.min_chunk_size:
                chunk = self._make_chunk(
                    current_text,
                    page_num,
                    current_para,
                    current_line_start,
                    current_line_start + current_text.count("\n"),
                    base_offset,
                    source_id,
                )
                chunks.append(chunk)

            # If single paragraph is larger than target_size, split it
            if len(para_text) > target_size:
                sub_chunks = self._split_large_paragraph(
                    para_text,
                    page_num,
                    para_idx,
                    self._count_lines_before(page_text, para_offset),
                    base_offset + para_offset,
                    source_id,
                    target_size,
                    overlap,
                )
                chunks.extend(sub_chunks)
                current_text = ""
            else:
                current_text = para_text
                current_para = para_idx
                current_line_start = self._count_lines_before(
                    page_text, para_offset
                )

            para_offset += len(para_text) + 2

        # Emit final chunk
        if current_text and len(current_text) >= self.min_chunk_size:
            chunk = self._make_chunk(
                current_text,
                page_num,
                current_para,
                current_line_start,
                current_line_start + current_text.count("\n"),
                base_offset,
                source_id,
            )
            chunks.append(chunk)

        return chunks

    def _split_large_paragraph(
        self,
        para_text: str,
        page_num: int,
        para_idx: int,
        line_start: int,
        char_offset: int,
        source_id: str,
        target_size: int,
        overlap: int,
    ) -> list[Chunk]:
        """Split a paragraph that's larger than target_size.

        Uses a sliding window with configurable overlap.

        Args:
            para_text: The paragraph text to split
            page_num: 1-based page number
            para_idx: 1-based paragraph index
            line_start: 1-based starting line number within page
            char_offset: Character offset within the full document
            source_id: Source identifier for chunk ID generation
            target_size: Target chunk size in characters
            overlap: Overlap between chunks in characters

        Returns:
            List of Chunk objects
        """
        chunks: list[Chunk] = []
        start = 0
        step = target_size - overlap

        while start < len(para_text):
            end = start + target_size
            chunk_text = para_text[start:end]

            if start > 0 and len(chunk_text.strip()) < self.min_chunk_size:
                break

            chunk = self._make_chunk(
                chunk_text,
                page_num,
                para_idx,
                line_start + para_text[:start].count("\n"),
                line_start + para_text[:end].count("\n"),
                char_offset + start,
                source_id,
            )
            chunks.append(chunk)

            start += step

        return chunks

    # ── Helpers ────────────────────────────────────────────────────────

    def _make_chunk(
        self,
        text: str,
        page: int,
        paragraph: int,
        line_start: int,
        line_end: int,
        char_offset: int,
        source_id: str,
    ) -> Chunk:
        """Create a Chunk object with a deterministic source hash.

        Args:
            text: Chunk text content
            page: 1-based page number
            paragraph: 1-based paragraph number
            line_start: 1-based starting line
            line_end: 1-based ending line
            char_offset: Character offset in full document
            source_id: Source identifier

        Returns:
            A new Chunk instance
        """
        return Chunk(
            text=text,
            page=page,
            paragraph=paragraph,
            line_start=line_start,
            line_end=line_end,
            char_offset=char_offset,
            char_count=len(text),
            algorithm_version=self.ALGORITHM_VERSION,
            source_hash=hashlib.sha256(text.encode()).hexdigest()[:16],
        )

    @staticmethod
    def _count_lines_before(page_text: str, offset: int) -> int:
        """Count the number of newlines before a character offset, +1 for 1-based.

        Args:
            page_text: Full page text
            offset: Character offset within the page

        Returns:
            1-based line number at the given offset
        """
        if offset <= 0:
            return 1
        offset = min(len(page_text), offset)
        return page_text[:offset].count("\n") + 1

    def _merge_tiny_chunks(self, chunks: list[Chunk]) -> list[Chunk]:
        """Merge trailing chunks that are smaller than min_chunk_size.

        Args:
            chunks: List of chunks to process

        Returns:
            List with tiny trailing chunks merged into the previous chunk
        """
        result = list(chunks)
        while len(result) >= 2 and result[-1].char_count < self.min_chunk_size:
            last = result.pop()
            prev = result.pop()
            merged = Chunk(
                text=prev.text + "\n\n" + last.text,
                page=prev.page,
                paragraph=prev.paragraph,
                line_start=prev.line_start,
                line_end=last.line_end,
                char_offset=prev.char_offset,
                char_count=prev.char_count + last.char_count + 2,
                algorithm_version=prev.algorithm_version,
                source_hash=hashlib.sha256(
                    (prev.text + last.text).encode()
                ).hexdigest()[:16],
            )
            result.append(merged)
        return result

    # ── Representation ─────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"Chunker(algorithm={self.ALGORITHM_NAME}, "
            f"version={self.ALGORITHM_VERSION}, "
            f"min_chunk_size={self.min_chunk_size})"
        )
