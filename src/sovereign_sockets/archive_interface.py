"""Sovereign Archive — R2 archive format interface for disaster recovery.

The Sovereign Archive is an optional, client-side-encrypted replica of the local
Canon substrate and VoxelCube state, stored in Cloudflare R2 for disaster
recovery.

**The R2 archive is subordinate to the local Canon.** The cloud is the
preservation layer, not the active brain. The local Canon remains authoritative
at all times. If there is any discrepancy, the local Canon wins.

Encryption:
- Algorithm: AES-256-GCM
- Keys: retained locally, **never in Cloudflare**
- Key fingerprint: stored in entry metadata for audit (first 8 hex chars of
  key hash)
- The Worker **never decrypts** vector data

Privacy:
- Pseudonymous label hashes (SHA-256) — NO raw labels, NO exact timestamps,
  NO PII
- Coarse time buckets (e.g. "2026-W33") instead of exact timestamps
- Engram IDs are opaque identifiers

Immutability:
- R2 objects are immutable after write — overwrites of existing engram IDs
  are rejected (HTTP 409)
- WORM (write-once, read-many) enforced at both application and storage layers
- The Canon's append-only contract is preserved

Restore procedure:
1. Verify the current chain-head hash and entry count
2. Restore encrypted Canon entries by engram ID
3. Decrypt vectors locally using retained AES-256-GCM keys
4. Verify each entry's Merkle hash: SHA-256(previous_hash + label + vector_bytes)
5. Verify the entire Merkle chain from genesis to chain head
6. If chain verification passes, import entries to the local Canon
7. If chain verification fails, **do not import** — investigate the discrepancy

This is a BLANK abstract interface (sovereign socket pattern). The proprietary
Brain provides the concrete implementation. No implementation lives here — only
the contract.

#C Adapted from SOVEREIGN_ARCHIVE_SPEC.md
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ARCHIVE_BUCKET = "nous-sovereign-archive"
ARCHIVE_BINDING = "SOVEREIGN_ARCHIVE"
ARCHIVE_SCHEMA_VERSION = "1.0.0"
ENCRYPTION_ALGORITHM = "AES-256-GCM"
MAX_OBJECT_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB, enforced by Worker
R2_METADATA_LIMIT_BYTES = 10 * 1024  # 10 KiB, Cloudflare R2 limit

# Worker routes (all require X-Nous-Internal-Secret header for airlock auth)
ROUTES: dict[str, dict[str, str]] = {
    "ingest": {
        "path": "/v1/archive/canon/ingest",
        "method": "POST",
        "gate": "CLOUD_SYNC_ENABLED",
    },
    "verify": {
        "path": "/v1/archive/canon/verify",
        "method": "POST",
        "gate": "none",
    },
    "restore": {
        "path": "/v1/archive/canon/restore",
        "method": "POST",
        "gate": "CLOUD_SYNC_ENABLED",
    },
    "snapshot": {
        "path": "/v1/archive/voxel/snapshot",
        "method": "POST",
        "gate": "CLOUD_SYNC_ENABLED",
    },
    "status": {
        "path": "/v1/archive/status",
        "method": "GET",
        "gate": "none",
    },
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ArchiveEntry:
    """A single Canon entry prepared for archive ingestion.

    Contains metadata only — **NO raw labels, NO exact timestamps, NO PII**.
    The vector payload is client-side-encrypted and opaque to the Worker.

    Attributes:
        engram_id: Opaque identifier for the Canon entry (e.g. "engram-abc123").
        pseudonymous_label_hash: SHA-256 hash of the raw label — never the
            raw label itself.
        merkle_hash: SHA-256 of (previous_hash + label + vector_bytes) —
            the chain link hash.
        previous_hash: The merkle_hash of the preceding entry in the chain,
            or a genesis sentinel for the first entry.
        coarse_time_bucket: Coarse time identifier (e.g. "2026-W33") — never
            an exact timestamp.
        schema_version: Archive schema version string (e.g. "1.0.0").
        enforcement_level: The Canon enforcement level (e.g. "CANON").
        vector_hex: Client-side AES-256-GCM encrypted FHRR vector bytes,
            hex-encoded. The Worker never decrypts this.
        key_fingerprint: First 8 hex chars of the key hash, for audit.
    """

    engram_id: str
    pseudonymous_label_hash: str
    merkle_hash: str
    previous_hash: str
    coarse_time_bucket: str
    schema_version: str
    enforcement_level: str
    vector_hex: str
    key_fingerprint: str


@dataclass
class MerkleProof:
    """Chain proof for local verification after restore.

    Allows the client to verify that a restored entry is correctly linked in
    the Merkle chain without trusting the cloud.

    Attributes:
        engram_id: The entry this proof belongs to.
        merkle_hash: SHA-256 of (previous_hash + label + vector_bytes).
        previous_hash: The preceding entry's merkle_hash, or genesis sentinel.
        chain_position: Zero-based position of this entry in the chain.
    """

    engram_id: str
    merkle_hash: str
    previous_hash: str
    chain_position: int


@dataclass
class RestoreResult:
    """The outcome of a restore operation.

    Attributes:
        entries: The restored ArchiveEntry list (encrypted — client decrypts
            locally).
        proofs: MerkleProof for each entry, for chain verification.
        chain_head_hash: The chain head hash at the time of restore.
        entry_count: Total entries in the archive at restore time.
        schema_version: Archive schema version.
        verified: True if the full Merkle chain verified locally from genesis
            to chain head. If False, entries MUST NOT be imported —
            investigate the discrepancy.
        discrepancy: Description of any chain verification failure, else None.
    """

    entries: list[ArchiveEntry]
    proofs: list[MerkleProof]
    chain_head_hash: str
    entry_count: int
    schema_version: str
    verified: bool
    discrepancy: str | None = None


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class ArchiveInterface(ABC):
    """Sovereign Archive — the R2 disaster recovery contract.

    The proprietary implementation lives in /proprietary_core/ (gitignored).
    The public Tool may only interact with the archive through this interface.

    All routes are gated behind `CLOUD_SYNC_ENABLED` (default: disabled) where
    applicable, and require `X-Nous-Internal-Secret` header for airlock auth.

    Critical invariants:
    - The R2 archive is *subordinate* to the local Canon. The cloud is the
      preservation layer, not the active brain.
    - Keys are retained locally, **never in Cloudflare**. The Worker never
      decrypts vector data.
    - R2 objects are immutable after write — overwrites are rejected (HTTP 409).
    - No raw labels, no exact timestamps, no PII ever leave the client.
    - Always verify the Merkle chain locally after restore. If there is any
      discrepancy, the local Canon wins.
    """

    @abstractmethod
    def ingest(self, entries: list[ArchiveEntry]) -> list[str]:
        """Accept encrypted Canon entries into the R2 archive.

        Each entry is written as an immutable R2 object. Overwrites of existing
        engram IDs are rejected (HTTP 409) — WORM enforcement at both the
        application and storage layers.

        Args:
            entries: List of ArchiveEntry objects with encrypted vector_hex,
                pseudonymous label hashes, and coarse time buckets. No raw
                labels, no exact timestamps, no PII.

        Returns:
            List of engram IDs successfully ingested. Entries that already
            exist (duplicate engram_id) are rejected and excluded from the
            return list.

        Gate: CLOUD_SYNC_ENABLED must be true. All objects must be <= 10 MB.
        """
        ...

    @abstractmethod
    def verify(self, proofs: list[MerkleProof]) -> bool:
        """Verify Merkle chain proofs against the archive's chain head.

        Returns the current chain-head hash and entry count, then checks each
        provided proof against the chain. This is a read-only operation — no
        gate required.

        Args:
            proofs: List of MerkleProof objects to verify.

        Returns:
            True if all proofs are valid and the chain is consistent from
            genesis to chain head. False if any proof fails or the chain is
            broken — in which case restore MUST NOT proceed.
        """
        ...

    @abstractmethod
    def restore(self, since: str) -> list[ArchiveEntry]:
        """Restore encrypted Canon entries from the R2 archive.

        Returns encrypted entries with coarse time buckets >= `since`. The
        client decrypts vectors locally using retained AES-256-GCM keys and
        verifies the Merkle chain before importing to the local Canon.

        Args:
            since: A coarse time bucket boundary (e.g. "2026-W33"). Only
                entries with coarse_time_bucket >= since are returned.

        Returns:
            List of ArchiveEntry objects with encrypted vector_hex. The client
            MUST:
            1. Decrypt vector_hex locally using retained AES-256-GCM key
            2. Verify merkle_hash = SHA-256(previous_hash + label + vector_bytes)
            3. Verify previous_hash matches expected chain position
            4. Verify the entire Merkle chain from genesis to chain head
            5. Import to local Canon only if chain verification passes

        If chain verification fails, do NOT import — investigate the
        discrepancy. The local Canon wins.

        Gate: CLOUD_SYNC_ENABLED must be true.
        """
        ...

    @abstractmethod
    def snapshot(self) -> str:
        """Accept an encrypted VoxelCube state snapshot into the R2 archive.

        Stores the snapshot as an immutable R2 object under the voxel/ prefix.
        The snapshot is client-side-encrypted — the Worker never decrypts it.

        Returns:
            The snapshot identifier (e.g. "snapshot-42") for tracking.

        Gate: CLOUD_SYNC_ENABLED must be true. Snapshot must be <= 10 MB.
        """
        ...

    @abstractmethod
    def status(self) -> dict[str, Any]:
        """Return archive health and metadata.

        A read-only operation — no gate required.

        Returns:
            A dict with keys:
            - "chain_head_hash": Current chain head hash.
            - "entry_count": Total Canon entries in the archive.
            - "schema_version": Archive schema version.
            - "encryption_algorithm": "AES-256-GCM".
            - "cloud_sync_enabled": Whether CLOUD_SYNC_ENABLED is true.
            - "last_operation": Timestamp of the last archive operation.
            - "bucket": The R2 bucket name.
        """
        ...
