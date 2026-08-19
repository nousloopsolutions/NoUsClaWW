"""Untrusted file boundary — path traversal, archive limits, MIME detection, quarantine.

Every file entering the NoUsClaWW agent stack must pass through this security
gate before being processed. Files that fail any check are quarantined, not
partially trusted. Retrieved/OCR/transcribed text is marked as untrusted and
cannot modify prompts, policies, tools, or files.

Architecture:
  1. FileSecurity — validates paths, checks archives, detects MIME, quarantines
  2. UntrustedContentMarker — tracks untrusted content by hash, enforces
     modification restrictions
  3. FileSecurityConfig — declarative configuration for all limits

Invariants:
  1. Path traversal and symlink escape are blocked before any processing
  2. Archive expansion limits are enforced BEFORE extraction, not after
  3. MIME detection uses content bytes, not file extension
  4. Quarantine is used instead of partial trust on malformed input
  5. Untrusted content cannot modify prompts, policies, tools, or files

SYNTH:
    purpose: Enforce untrusted file boundary with path traversal protection, archive limits, content-based MIME detection, and quarantine — never partially trust malformed input
    axioms: [local_first, epistemic_boundary, honest_failure_over_fake_success, evidence_over_intuition, reversibility_awareness]
    objective: Every file passes security validation before processing; path traversal and symlink escapes are blocked; archive bombs are detected before extraction; MIME is detected from content bytes; untrusted content cannot modify the system
    anti_patterns:
        - Trusting file extensions for MIME type (must detect from content bytes)
        - Partial trust for malformed files (quarantine instead)
        - Allowing untrusted content to modify prompts, policies, tools, or files
        - Enforcing archive limits after extraction (must check before)
        - Allowing path traversal or symlink escape to reach files outside allowed roots
        - Silently passing files that fail security checks
"""
#C Adapted from NoUs-fordge Nous-hub mvp_local_core data/file_security.py

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Exceptions ──────────────────────────────────────────────────────────────

class FileSecurityError(Exception):
    """Base class for file security violations."""


class PathTraversalError(FileSecurityError):
    """Raised when a path attempts to escape the allowed root."""


class SymlinkEscapeError(FileSecurityError):
    """Raised when a symlink points outside the allowed root."""


class FileSizeError(FileSecurityError):
    """Raised when a file exceeds the size limit."""


class FileCountError(FileSecurityError):
    """Raised when too many files are in an archive."""


class DecompressionBombError(FileSecurityError):
    """Raised when decompression ratio exceeds safe limit."""


class MIMEMismatchError(FileSecurityError):
    """Raised when MIME type doesn't match content."""


class QuarantineError(FileSecurityError):
    """Raised when a file should be quarantined instead of processed."""


# ── Enums ───────────────────────────────────────────────────────────────────

class TrustLevel(Enum):
    """Trust level of file content.

    UNTRUSTED — Retrieved/OCR/transcribed text — cannot modify system
    SANDBOXED — Processed in isolated worker
    TRUSTED   — Passed all security checks
    """

    UNTRUSTED = "UNTRUSTED"
    SANDBOXED = "SANDBOXED"
    TRUSTED = "TRUSTED"


# ── Configuration ───────────────────────────────────────────────────────────

@dataclass
class FileSecurityConfig:
    """Configuration for file security checks.

    Attributes:
        max_file_size: Maximum file size in bytes
        max_archive_files: Maximum files in an archive
        max_decompression_ratio: Maximum decompression ratio (N:1)
        max_path_depth: Maximum path depth
        allowed_roots: Set of allowed root directories
        quarantine_dir: Directory for quarantined files
        high_risk_extensions: Extensions requiring worker isolation
    """

    max_file_size: int = 500 * 1024 * 1024  # 500 MB
    max_archive_files: int = 10_000
    max_decompression_ratio: int = 100
    max_path_depth: int = 32
    allowed_roots: set[str] = field(default_factory=set)
    quarantine_dir: str = "quarantine"
    high_risk_extensions: set[str] = field(
        default_factory=lambda: {
            ".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".7z",
            ".rar", ".cab", ".iso",
        }
    )


# ── MIME Signatures ─────────────────────────────────────────────────────────

# Content-based MIME detection signatures (magic bytes)
_MIME_SIGNATURES: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
    (b"%PDF", "application/pdf"),
    (b"PK\x03\x04", "application/zip"),
    (b"PK\x05\x06", "application/zip"),
    (b"PK\x07\x08", "application/zip"),
    (b"\x1f\x8b", "application/gzip"),
    (b"BZh", "application/x-bzip2"),
    (b"7z\xbc\xaf\x27\x1c", "application/x-7z-compressed"),
    (b"Rar!\x1a\x07", "application/x-rar-compressed"),
    (b"\x25\x50\x44\x46", "application/pdf"),
    (b"RIFF", "audio/wav"),
    (b"ID3", "audio/mpeg"),
    (b"\xff\xfb", "audio/mpeg"),
    (b"OggS", "audio/ogg"),
    (b"\x00\x00\x01\x00", "image/x-icon"),
    (b"\x00\x00\x02\x00", "image/x-icon"),
]

# Text MIME detection — check for common text byte patterns
_TEXT_INDICATORS = (
    b"<?xml",
    b"<!DOCTYPE",
    b"<!doctype",
    b"<html",
    b"<HTML",
)


def _detect_text_mime(content: bytes) -> str | None:
    """Detect text-based MIME types from content bytes.

    Args:
        content: First N bytes of the file

    Returns:
        MIME type string, or None if not recognized as text
    """
    if not content:
        return None

    # Check for XML/HTML signatures
    stripped = content.lstrip()
    for sig in _TEXT_INDICATORS:
        if stripped.startswith(sig):
            if sig in (b"<?xml",):
                return "text/xml"
            return "text/html"

    # Check if content is valid UTF-8 text
    try:
        decoded = content.decode("utf-8")
        # If it decodes cleanly and has mostly printable chars, it's text
        printable = sum(
            1 for c in decoded if c.isprintable() or c in "\n\r\t "
        )
        if len(decoded) > 0 and printable / len(decoded) > 0.8:
            return "text/plain"
    except (UnicodeDecodeError, UnicodeError):
        pass

    return None


# ── File Security ───────────────────────────────────────────────────────────

class FileSecurity:
    """Untrusted file boundary enforcement.

    Every file must pass through this gate before being processed.
    Files that fail any check are quarantined, not partially trusted.

    Usage::

        config = FileSecurityConfig(allowed_roots={"/data/input"})
        gate = FileSecurity(config)
        gate.validate_path("/data/input/test.pdf")  # OK
        gate.validate_path("/data/input/../../../etc/passwd")  # Raises
    """

    def __init__(self, config: FileSecurityConfig | None = None) -> None:
        """Initialize the file security gate.

        Args:
            config: Security configuration (defaults used if None)
        """
        self.config = config or FileSecurityConfig()
        os.makedirs(self.config.quarantine_dir, exist_ok=True)

    def validate_path(self, path: str) -> TrustLevel:
        """Validate a file path against all security checks.

        Args:
            path: Path to the file to validate

        Returns:
            TrustLevel.SANDBOXED if all checks pass

        Raises:
            PathTraversalError: If path escapes allowed root
            SymlinkEscapeError: If symlink points outside allowed root
            FileSizeError: If file is too large
            FileSecurityError: If path depth exceeds limit
        """
        # 1. Path traversal protection
        self._check_path_traversal(path)

        # 2. Symlink escape protection
        self._check_symlink_escape(path)

        # 3. Path depth check
        self._check_path_depth(path)

        # 4. File size check
        self._check_file_size(path)

        # 5. High-risk extension check
        ext = os.path.splitext(path)[1].lower()
        if ext in self.config.high_risk_extensions:
            return TrustLevel.SANDBOXED

        return TrustLevel.SANDBOXED

    def check_archive(
        self,
        archive_path: str,
        file_count: int,
        total_uncompressed_size: int,
        compressed_size: int,
    ) -> TrustLevel:
        """Check archive expansion limits before extraction.

        Limits checked:
          - File count (max_archive_files)
          - Decompression ratio (max_decompression_ratio — zip bomb protection)
          - Total uncompressed size (max_file_size)

        Args:
            archive_path: Path to the archive (for error messages)
            file_count: Number of files in archive
            total_uncompressed_size: Total uncompressed size in bytes
            compressed_size: Compressed size in bytes

        Returns:
            TrustLevel.SANDBOXED if all checks pass

        Raises:
            FileCountError: If too many files
            DecompressionBombError: If decompression ratio too high
            FileSizeError: If total size too large
        """
        # Check file count
        if file_count > self.config.max_archive_files:
            raise FileCountError(
                f"Archive {archive_path} contains {file_count} files — "
                f"limit is {self.config.max_archive_files}"
            )

        # Check decompression ratio (zip bomb protection)
        if compressed_size > 0:
            ratio = total_uncompressed_size / compressed_size
            if ratio > self.config.max_decompression_ratio:
                raise DecompressionBombError(
                    f"Decompression ratio {ratio:.1f}:1 exceeds limit "
                    f"of {self.config.max_decompression_ratio}:1 — "
                    f"possible zip bomb in {archive_path}"
                )

        # Check total uncompressed size
        if total_uncompressed_size > self.config.max_file_size:
            raise FileSizeError(
                f"Archive {archive_path} total uncompressed size "
                f"{total_uncompressed_size} bytes — "
                f"limit is {self.config.max_file_size} bytes"
            )

        return TrustLevel.SANDBOXED

    def detect_mime(self, content: bytes) -> str:
        """Detect MIME type from content bytes (not extension).

        Uses magic byte signatures for binary formats and UTF-8 decoding
        heuristics for text formats.

        Args:
            content: First N bytes of the file content (>= 16 recommended)

        Returns:
            Detected MIME type string (e.g., "application/pdf")
        """
        if not content:
            return "application/octet-stream"

        # Check binary signatures
        for signature, mime_type in _MIME_SIGNATURES:
            if content.startswith(signature):
                return mime_type

        # Check text-based formats
        text_mime = _detect_text_mime(content)
        if text_mime:
            return text_mime

        return "application/octet-stream"

    def check_mime_mismatch(
        self,
        file_path: str,
        declared_mime: str,
        detected_mime: str,
    ) -> None:
        """Check for MIME/content mismatch.

        Args:
            file_path: Path to the file (for error messages)
            declared_mime: MIME type declared by extension
            detected_mime: MIME type detected from content

        Raises:
            MIMEMismatchError: If declared and detected don't match
        """
        if declared_mime != detected_mime:
            # Allow text/* variants to match each other
            if declared_mime.startswith("text/") and detected_mime.startswith("text/"):
                return
            raise MIMEMismatchError(
                f"MIME mismatch for {file_path}: "
                f"declared={declared_mime}, detected={detected_mime}"
            )

    def quarantine(self, path: str, reason: str = "") -> str:
        """Move a file to quarantine instead of processing it.

        Args:
            path: Path to the file to quarantine
            reason: Why the file is being quarantined

        Returns:
            Path to the quarantined file
        """
        basename = os.path.basename(path)
        quarantine_path = os.path.join(self.config.quarantine_dir, basename)

        # Avoid overwriting existing quarantined files
        counter = 0
        while os.path.exists(quarantine_path):
            counter += 1
            name, ext = os.path.splitext(basename)
            quarantine_path = os.path.join(
                self.config.quarantine_dir, f"{name}_{counter}{ext}"
            )

        shutil.move(path, quarantine_path)
        return quarantine_path

    # ── Internal Checks ────────────────────────────────────────────────

    def _check_path_traversal(self, file_path: str) -> None:
        """Check for path traversal attacks (../, ..\\).

        Args:
            file_path: Path to check

        Raises:
            PathTraversalError: If traversal detected or path outside allowed roots
        """
        normalized = os.path.normpath(os.path.abspath(file_path))

        # Check for traversal patterns in the original path
        components = file_path.replace("\\", "/").split("/")
        if ".." in components:
            raise PathTraversalError(
                f"Path traversal detected in {file_path} — "
                "'..' components not allowed"
            )

        # Check against allowed roots
        if self.config.allowed_roots:
            allowed = False
            for root in self.config.allowed_roots:
                root_abs = os.path.abspath(root)
                if normalized.startswith(root_abs):
                    allowed = True
                    break
            if not allowed:
                raise PathTraversalError(
                    f"Path {normalized} is outside allowed roots: "
                    f"{self.config.allowed_roots}"
                )

    def _check_symlink_escape(self, file_path: str) -> None:
        """Check if a symlink points outside the allowed root.

        Args:
            file_path: Path to check

        Raises:
            SymlinkEscapeError: If symlink target is outside allowed roots
        """
        if not os.path.islink(file_path):
            return

        link_target = os.path.realpath(file_path)

        if self.config.allowed_roots:
            for root in self.config.allowed_roots:
                root_abs = os.path.abspath(root)
                if not link_target.startswith(root_abs):
                    raise SymlinkEscapeError(
                        f"Symlink {file_path} points to {link_target} — "
                        f"outside allowed root {root_abs}"
                    )

    def _check_path_depth(self, file_path: str) -> None:
        """Check if path depth is within limits.

        Args:
            file_path: Path to check

        Raises:
            FileSecurityError: If path depth exceeds limit
        """
        normalized = os.path.normpath(file_path)
        depth = len(normalized.replace("\\", "/").split("/"))
        if depth > self.config.max_path_depth:
            raise FileSecurityError(
                f"Path depth {depth} exceeds limit of "
                f"{self.config.max_path_depth}"
            )

    def _check_file_size(self, file_path: str) -> None:
        """Check if file size is within limits.

        Args:
            file_path: Path to check

        Raises:
            FileSizeError: If file is too large
        """
        if not os.path.exists(file_path):
            return  # Size check will fail at open time
        size = os.path.getsize(file_path)
        if size > self.config.max_file_size:
            raise FileSizeError(
                f"File size {size} bytes exceeds limit of "
                f"{self.config.max_file_size} bytes"
            )


# ── Untrusted Content Marker ────────────────────────────────────────────────

class UntrustedContentMarker:
    """Marks retrieved/OCR/transcribed text as UNTRUSTED.

    Ensures that content extracted from untrusted sources is tracked and
    prevented from modifying system configuration. Untrusted content
    cannot modify prompts, policies, tools, or files.

    Usage::

        marker = UntrustedContentMarker()
        content_id = marker.mark_untrusted(ocr_text)
        # Later, when content tries to modify something:
        marker.check_can_modify(content_id, "prompt")  # Raises
    """

    def __init__(self) -> None:
        self._untrusted_content: set[str] = set()

    def mark_untrusted(self, content: str) -> str:
        """Mark content as untrusted.

        Args:
            content: The content to mark

        Returns:
            A content ID (SHA-256 hash) for tracking
        """
        content_id = hashlib.sha256(content.encode()).hexdigest()
        self._untrusted_content.add(content_id)
        return content_id

    def is_untrusted(self, content_id: str) -> bool:
        """Check if content is marked as untrusted.

        Args:
            content_id: Content ID returned by mark_untrusted()

        Returns:
            True if the content is marked untrusted
        """
        return content_id in self._untrusted_content

    def check_can_modify(self, content_id: str, target: str) -> None:
        """Check if untrusted content can modify a target.

        Args:
            content_id: ID of the content attempting modification
            target: What is being modified (prompt/policy/tool/file)

        Raises:
            FileSecurityError: If untrusted content tries to modify system
        """
        if self.is_untrusted(content_id):
            raise FileSecurityError(
                f"UNTRUSTED content (id={content_id[:16]}...) cannot modify "
                f"{target} — untrusted content cannot modify prompts, "
                f"policies, tools, or files"
            )

    def clear(self) -> None:
        """Clear all untrusted content markers."""
        self._untrusted_content.clear()

    def count(self) -> int:
        """Return the number of untrusted content items tracked."""
        return len(self._untrusted_content)
