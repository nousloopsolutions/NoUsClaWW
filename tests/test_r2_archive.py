"""Tests for the R2Archive — community ArchiveInterface implementation."""

from __future__ import annotations

import hashlib

import pytest

from sovereign_sockets.archive_interface import (
    ArchiveEntry,
    ArchiveInterface,
    MerkleProof,
    RestoreResult,
    ARCHIVE_SCHEMA_VERSION,
)


def test_r2_archive_implements_interface():
    """R2Archive must implement ArchiveInterface."""
    from nousclaww.archive.r2_archive import R2Archive

    archive = R2Archive()
    assert isinstance(archive, ArchiveInterface)


def test_r2_archive_encrypt_decrypt_roundtrip():
    """Encryption and decryption should roundtrip correctly."""
    from nousclaww.archive.r2_archive import R2Archive

    archive = R2Archive()
    plaintext = b"sensitive vector data"
    encrypted = archive._encrypt(plaintext)
    decrypted = archive._decrypt(encrypted)
    assert decrypted == plaintext


def test_r2_archive_create_entry():
    """create_entry should produce a valid ArchiveEntry with encryption and Merkle hash."""
    from nousclaww.archive.r2_archive import R2Archive

    archive = R2Archive()
    entry = archive.create_entry(
        engram_id="engram-001",
        label="test label",
        vector_bytes=b"vector data",
    )

    assert entry.engram_id == "engram-001"
    assert entry.pseudonymous_label_hash == hashlib.sha256(b"test label").hexdigest()
    assert entry.previous_hash == R2Archive.GENESIS_HASH
    assert entry.vector_hex != "vector data"  # should be encrypted
    assert entry.key_fingerprint
    assert entry.schema_version == ARCHIVE_SCHEMA_VERSION
    assert entry.enforcement_level == "CANON"


def test_r2_archive_ingest_and_entry_count():
    """Ingesting entries should increase the entry count."""
    from nousclaww.archive.r2_archive import R2Archive

    archive = R2Archive()
    entry1 = archive.create_entry("eng-1", "label1", b"data1")
    archive.ingest([entry1])
    entry2 = archive.create_entry("eng-2", "label2", b"data2")
    archive.ingest([entry2])

    assert archive.entry_count == 2


def test_r2_archive_ingest_duplicate_rejected():
    """Duplicate engram_ids should be rejected."""
    from nousclaww.archive.r2_archive import R2Archive

    archive = R2Archive()
    entry = archive.create_entry("eng-1", "label1", b"data1")
    archive.ingest([entry])

    # Create a new entry with the same engram_id
    entry2 = archive.create_entry("eng-1", "label2", b"data2")
    ingested = archive.ingest([entry2])
    assert len(ingested) == 0  # duplicate rejected
    assert archive.entry_count == 1


def test_r2_archive_verify_chain():
    """Verify should return True for a valid Merkle chain."""
    from nousclaww.archive.r2_archive import R2Archive

    archive = R2Archive()
    # Create and ingest entries one at a time so the chain head is updated
    entry1 = archive.create_entry("eng-1", "label1", b"data1")
    archive.ingest([entry1])
    entry2 = archive.create_entry("eng-2", "label2", b"data2")
    archive.ingest([entry2])

    proofs = [
        MerkleProof(engram_id="eng-1", merkle_hash=entry1.merkle_hash,
                    previous_hash=entry1.previous_hash, chain_position=0),
        MerkleProof(engram_id="eng-2", merkle_hash=entry2.merkle_hash,
                    previous_hash=entry2.previous_hash, chain_position=1),
    ]
    assert archive.verify(proofs)


def test_r2_archive_verify_broken_chain():
    """Verify should return False for a broken Merkle chain."""
    from nousclaww.archive.r2_archive import R2Archive

    archive = R2Archive()
    entry1 = archive.create_entry("eng-1", "label1", b"data1")
    archive.ingest([entry1])

    proofs = [
        MerkleProof(engram_id="eng-1", merkle_hash="wrong_hash",
                    previous_hash=entry1.previous_hash, chain_position=0),
    ]
    assert not archive.verify(proofs)


def test_r2_archive_restore():
    """Restore should return entries with time bucket >= since."""
    from nousclaww.archive.r2_archive import R2Archive

    archive = R2Archive()
    entry = archive.create_entry("eng-1", "label1", b"data1")
    archive.ingest([entry])

    # Restore all entries (use a very early bucket)
    restored = archive.restore("1900-W01")
    assert len(restored) == 1
    assert restored[0].engram_id == "eng-1"

    # Restore with a future bucket should return nothing
    restored = archive.restore("9999-W01")
    assert len(restored) == 0


def test_r2_archive_snapshot():
    """Snapshot should return a snapshot ID."""
    from nousclaww.archive.r2_archive import R2Archive

    archive = R2Archive()
    snap_id = archive.snapshot()
    assert snap_id.startswith("snapshot-")


def test_r2_archive_chain_head_progression():
    """The chain head should progress as entries are ingested."""
    from nousclaww.archive.r2_archive import R2Archive

    archive = R2Archive()
    assert archive.chain_head == R2Archive.GENESIS_HASH

    entry1 = archive.create_entry("eng-1", "label1", b"data1")
    archive.ingest([entry1])
    assert archive.chain_head == entry1.merkle_hash

    # Create entry2 after ingesting entry1 so it chains to entry1
    entry2 = archive.create_entry("eng-2", "label2", b"data2")
    archive.ingest([entry2])
    assert archive.chain_head == entry2.merkle_hash


def test_r2_archive_key_fingerprint():
    """The key fingerprint should be the first 8 hex chars of SHA-256(key)."""
    from nousclaww.archive.r2_archive import R2Archive, _key_fingerprint

    key = b"test-key-" + b"0" * 23  # 32 bytes
    archive = R2Archive(encryption_key=key)
    expected_fp = hashlib.sha256(key).hexdigest()[:8]
    assert archive._key_fp == expected_fp
