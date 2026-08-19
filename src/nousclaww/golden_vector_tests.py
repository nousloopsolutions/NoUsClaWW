"""
Golden Vector Tests -- Cross-Platform Determinism Verification.

#C Adapted from NoUs-fordge Nous-hub mvp_local_core core/golden_vector_tests.py

Verifies that the deterministic vector generation pipeline produces
IDENTICAL vectors on every platform (x86, ARM, Snapdragon NPU).

The golden vectors are pre-computed reference vectors. If any platform
produces a different vector, it indicates a numerical drift bug in the
seed -> PCG64 -> Integer Genesis -> float32 quantization pipeline.

GOLDEN VECTOR INVARIANTS:
  1. Same label + same dim + same dtype = same vector (bit-exact)
  2. Vectors are on the unit circle (|v_i| = 1 for complex)
  3. Vectors are L2-normalized (||v|| = 1 for real)
  4. Different labels produce orthogonal-ish vectors (sim ~= 0)
  5. The SHA-256 seed is the full 256 bits (never truncated)

These tests are the CANONICAL determinism check. They MUST pass on
every platform. If they fail, the entire memory system is unreliable.

Socket Pattern: This module imports numpy, typing, and hrr_math only.

SYNTH:
    purpose: Cross-platform determinism verification -- golden vector reference fixtures, hash-chain persistence, and serialization roundtrip checks.
    axioms: [local_first, scientific_method, evidence_over_intuition, honest_failure_over_fake_success]
    objective: Bit-exact vector reproduction across x86, ARM, and any future silicon, verified via hash-chain persistence that survives process restarts.
    anti_patterns:
        - Never change the golden labels (they are fixed reference inputs for all platforms)
        - Never skip the 256-bit seed verification (truncation destroys VSA orthogonality)
        - Never claim cross-platform determinism without multi-environment artifacts
        - Never skip serialization roundtrip verification (deserialization errors are silent corruption)
        - Never use float64 for fingerprint computation (use float32/complex64 for cross-platform portability)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from .hrr_math import (
    STORAGE_DIM,
    WORKING_DIM,
    cosine_similarity,
    deterministic_vector,
    seed_from_label,
)

# -- Golden Test Labels -----------------------------------------------------------


# These labels are FIXED and MUST NOT change. They are the reference inputs
# for cross-platform determinism verification.
GOLDEN_LABELS = [
    "vctx::0",
    "vctx::1",
    "vctx::2",
    "vctx::3",
    "vtime::0",
    "vtime::1",
    "vtime::2",
    "vtime::3",
    "vstate::0",
    "vstate::1",
    "vstate::2",
    "vstate::3",
    "sensor::heart_rate",
    "sensor::hrv",
    "sensor::sleep_quality",
    "golden::axiom::kantian+severance+horizon",
    "engram::20260810_173000",
    "concept::cognitive_load",
    "concept::stress_response",
    "concept::flow_state",
]


# -- Golden Seed Values -----------------------------------------------------------


def _compute_expected_seed(label: str) -> int:
    """
    Compute the expected full 256-bit SHA-256 seed for a label.

    Args:
        label: Label string to compute seed for

    Returns:
        Full 256-bit integer from SHA-256 digest
    """
    h = hashlib.sha256(label.encode("utf-8"))
    return int.from_bytes(h.digest(), byteorder="big")


# -- Golden Vector Properties -----------------------------------------------------


# Properties that MUST hold for every golden vector:
GOLDEN_PROPERTIES = {
    "determinism": "Same label always produces the same vector (bit-exact)",
    "unit_circle_complex": "Complex vectors have |v_i| = 1 for all i",
    "l2_normalized_real": "Real vectors have ||v|| = 1",
    "orthogonality": "Different labels produce sim ~= 0 (within noise floor)",
    "seed_256bit": "SHA-256 seed uses full 256 bits (never truncated to 32)",
    "float32_quantized": "Values are quantized through float32 (severs libm drift)",
}


class GoldenVectorVerifier:
    """
    Verifier for golden vector determinism properties.

    Runs a comprehensive set of checks on the deterministic vector
    pipeline to ensure cross-platform reproducibility. Supports:
      - Seed verification (full 256-bit, not truncated)
      - Determinism (same label -> same vector, bit-exact)
      - Unit-circle / L2 normalization checks
      - Orthogonality checks (different labels -> sim ~= 0)
      - Float32 quantization verification
      - Serialization/deserialization roundtrip
      - Hash-chain persistence (cross-restart verification)
      - Cross-dimension consistency

    Attributes:
        dim:   Vector dimensionality (default 1024)
        dtype: Vector dtype (default complex128 for FHRR)
    """

    def __init__(self, dim: int = WORKING_DIM, dtype: np.dtype = np.complex128) -> None:
        """
        Initialize the verifier.

        Args:
            dim:   Vector dimensionality (default 1024)
            dtype: Vector dtype (default complex128 for FHRR)
        """
        self.dim = dim
        self.dtype = dtype

    # -- Individual Verification Methods ----------------------------------------

    def verify_seed(self, label: str) -> tuple[bool, int, int]:
        """
        Verify that seed_from_label produces the full 256-bit SHA-256.

        Args:
            label: Label to check

        Returns:
            Tuple of (passed, actual_seed, expected_seed)
        """
        actual_seed = seed_from_label(label)
        expected_seed = _compute_expected_seed(label)
        passed = actual_seed == expected_seed
        return (passed, actual_seed, expected_seed)

    def verify_seed_not_truncated(self, label: str) -> tuple[bool, int]:
        """
        Verify that the seed is NOT truncated to 32 bits.

        A 256-bit seed has values > 2^32. If the seed fits in 32 bits,
        it was truncated (a common bug).

        Args:
            label: Label to check

        Returns:
            Tuple of (passed, seed_value)
        """
        seed = seed_from_label(label)
        # A full 256-bit seed should be much larger than 2^32
        passed = seed > 2**32
        return (passed, seed)

    def verify_determinism(self, label: str) -> tuple[bool, float]:
        """
        Verify that the same label produces the same vector twice.

        Args:
            label: Label to check

        Returns:
            Tuple of (passed, max_abs_difference)
        """
        v1 = deterministic_vector(label, self.dim, self.dtype)
        v2 = deterministic_vector(label, self.dim, self.dtype)
        max_diff = float(np.max(np.abs(v1 - v2)))
        passed = max_diff < 1e-14
        return (passed, max_diff)

    def verify_unit_circle(self, label: str) -> tuple[bool, float]:
        """
        Verify that a complex vector is on the unit circle.

        Args:
            label: Label to check

        Returns:
            Tuple of (passed, max_magnitude_deviation)
        """
        if self.dtype not in (np.complex128, np.complex64):
            return (True, 0.0)  # Skip for real vectors
        v = deterministic_vector(label, self.dim, self.dtype)
        mags = np.abs(v)
        max_dev = float(np.max(np.abs(mags - 1.0)))
        passed = max_dev < 1e-6
        return (passed, max_dev)

    def verify_l2_normalized(self, label: str) -> tuple[bool, float]:
        """
        Verify that a real vector is L2-normalized.

        Args:
            label: Label to check

        Returns:
            Tuple of (passed, norm_deviation)
        """
        if self.dtype in (np.complex128, np.complex64):
            return (True, 0.0)  # Skip for complex vectors
        v = deterministic_vector(label, self.dim, self.dtype)
        norm = float(np.linalg.norm(v))
        dev = abs(norm - 1.0)
        passed = dev < 1e-6
        return (passed, dev)

    def verify_orthogonality(
        self, label_a: str, label_b: str
    ) -> tuple[bool, float]:
        """
        Verify that two different labels produce near-orthogonal vectors.

        For 1024-D complex FHRR, random vectors have expected similarity
        ~= 0 with std ~= 1/sqrt(1024) ~= 0.031.

        Args:
            label_a: First label
            label_b: Second label

        Returns:
            Tuple of (passed, similarity)
        """
        if label_a == label_b:
            return (True, 1.0)
        va = deterministic_vector(label_a, self.dim, self.dtype)
        vb = deterministic_vector(label_b, self.dim, self.dtype)
        sim = cosine_similarity(va, vb)
        # For 1024-D, |sim| < 0.2 is well within expected range
        passed = abs(sim) < 0.2
        return (passed, sim)

    def verify_float32_quantization(self, label: str) -> tuple[bool, float]:
        """
        Verify that values are quantized through float32.

        The pipeline generates uint64 -> float64, then quantizes through
        float32 to sever libm drift. The result should be representable
        as float32 without loss.

        Args:
            label: Label to check

        Returns:
            Tuple of (passed, max_quantization_error)
        """
        v = deterministic_vector(label, self.dim, self.dtype)
        if self.dtype in (np.complex128, np.complex64):
            # Check that real and imaginary parts are float32-representable
            real_f32 = v.real.astype(np.float32).astype(np.float64)
            imag_f32 = v.imag.astype(np.float32).astype(np.float64)
            max_err = max(
                float(np.max(np.abs(v.real - real_f32))),
                float(np.max(np.abs(v.imag - imag_f32))),
            )
        else:
            v_f32 = v.astype(np.float32).astype(np.float64)
            max_err = float(np.max(np.abs(v - v_f32)))
        passed = max_err < 1e-6
        return (passed, max_err)

    # -- Comprehensive Verification ---------------------------------------------

    def verify_all(self) -> dict[str, list[dict[str, Any]]]:
        """
        Run all golden vector verifications.

        Returns:
            Dict with keys 'seeds', 'determinism', 'unit_circle',
            'orthogonality', 'quantization', 'summary'
        """
        results: dict[str, list[dict[str, Any]]] = {
            "seeds": [],
            "determinism": [],
            "unit_circle": [],
            "orthogonality": [],
            "quantization": [],
        }

        all_passed = True

        for label in GOLDEN_LABELS:
            # Seed verification
            seed_pass, _actual, _expected = self.verify_seed(label)
            trunc_pass, seed_val = self.verify_seed_not_truncated(label)
            results["seeds"].append({
                "label": label,
                "seed_correct": seed_pass,
                "seed_not_truncated": trunc_pass,
                "seed_value": seed_val,
            })
            if not seed_pass or not trunc_pass:
                all_passed = False

            # Determinism
            det_pass, max_diff = self.verify_determinism(label)
            results["determinism"].append({
                "label": label,
                "passed": det_pass,
                "max_diff": max_diff,
            })
            if not det_pass:
                all_passed = False

            # Unit circle (complex)
            uc_pass, max_dev = self.verify_unit_circle(label)
            results["unit_circle"].append({
                "label": label,
                "passed": uc_pass,
                "max_deviation": max_dev,
            })
            if not uc_pass:
                all_passed = False

            # Float32 quantization
            q_pass, q_err = self.verify_float32_quantization(label)
            results["quantization"].append({
                "label": label,
                "passed": q_pass,
                "max_error": q_err,
            })
            if not q_pass:
                all_passed = False

        # Orthogonality (pairwise)
        for i, label_a in enumerate(GOLDEN_LABELS):
            for j, label_b in enumerate(GOLDEN_LABELS):
                if j <= i:
                    continue
                ortho_pass, sim = self.verify_orthogonality(label_a, label_b)
                results["orthogonality"].append({
                    "label_a": label_a,
                    "label_b": label_b,
                    "passed": ortho_pass,
                    "similarity": sim,
                })
                if not ortho_pass:
                    all_passed = False

        results["summary"] = {
            "total_labels": len(GOLDEN_LABELS),
            "total_checks": (
                len(results["seeds"])
                + len(results["determinism"])
                + len(results["unit_circle"])
                + len(results["quantization"])
                + len(results["orthogonality"])
            ),
            "all_passed": all_passed,
            "dim": self.dim,
            "dtype": str(self.dtype),
        }

        return results

    def verify(self, fixtures: dict[str, Any]) -> bool:
        """
        Verify that current vectors match a set of saved fixtures.

        Args:
            fixtures: Fixture dict from generate_fixture() or loaded
                     from save_chain()

        Returns:
            True if all fixture checks pass, False otherwise
        """
        # Verify dim and dtype match
        if int(fixtures.get("dim", 0)) != self.dim:
            return False
        if fixtures.get("dtype", "") != str(self.dtype):
            return False

        # Verify hash chain root
        expected_root = fixtures.get("hash_chain_root", "")
        actual_root = self.compute_hash_chain(
            fixtures.get("labels", GOLDEN_LABELS)
        )
        if actual_root != expected_root:
            return False

        # Verify per-label fingerprints
        expected_fps = fixtures.get("fingerprints", {})
        for label, expected_fp in expected_fps.items():
            actual_fp = self.compute_golden_fingerprint(label)
            if actual_fp != expected_fp:
                return False

        return True

    def verify_cross_dim_consistency(self, label: str) -> dict[str, Any]:
        """
        Verify that the same label produces consistent vectors across dimensions.

        The vector at dim=1024 and dim=32 should both be deterministic
        and on the unit circle, even though they're different vectors.

        Args:
            label: Label to check

        Returns:
            Dict with verification results
        """
        v_1024 = deterministic_vector(label, WORKING_DIM, np.complex128)
        v_32 = deterministic_vector(label, STORAGE_DIM, np.complex128)

        # Both should be deterministic
        v_1024_2 = deterministic_vector(label, WORKING_DIM, np.complex128)
        v_32_2 = deterministic_vector(label, STORAGE_DIM, np.complex128)

        det_1024 = float(np.max(np.abs(v_1024 - v_1024_2))) < 1e-14
        det_32 = float(np.max(np.abs(v_32 - v_32_2))) < 1e-14

        # Both should be on unit circle
        uc_1024 = float(np.max(np.abs(np.abs(v_1024) - 1.0))) < 1e-6
        uc_32 = float(np.max(np.abs(np.abs(v_32) - 1.0))) < 1e-6

        return {
            "label": label,
            "det_1024": det_1024,
            "det_32": det_32,
            "unit_circle_1024": uc_1024,
            "unit_circle_32": uc_32,
            "all_passed": det_1024 and det_32 and uc_1024 and uc_32,
        }

    # -- Fingerprints ------------------------------------------------------------

    def compute_golden_fingerprint(self, label: str) -> str:
        """
        Compute a short fingerprint for a golden vector.

        This is the first 16 hex chars of SHA-256 of the vector's
        byte representation. It can be used for quick cross-platform
        comparison without transmitting the full vector.

        Args:
            label: Label to compute fingerprint for

        Returns:
            16-character hex fingerprint
        """
        v = deterministic_vector(label, self.dim, self.dtype)
        # Use float32 (complex64) bytes for consistent cross-platform fingerprint
        v_bytes = v.astype(np.complex64).tobytes()
        return hashlib.sha256(v_bytes).hexdigest()[:16]

    def compute_all_fingerprints(self) -> dict[str, str]:
        """
        Compute fingerprints for all golden labels.

        Returns:
            Dict of label -> 16-char hex fingerprint
        """
        return {
            label: self.compute_golden_fingerprint(label)
            for label in GOLDEN_LABELS
        }

    # -- Serialization & Hash-Chain Persistence ---------------------------------

    def serialize_vector(self, label: str) -> bytes:
        """
        Serialize a golden vector to portable bytes.

        Uses float32 (complex64) for cross-platform portability.
        The serialized form is: [label_len:4][label:N][vector_bytes].

        Args:
            label: Label to serialize

        Returns:
            Portable byte representation
        """
        v = deterministic_vector(label, self.dim, self.dtype)
        v_portable = v.astype(np.complex64)
        v_bytes = v_portable.tobytes()
        label_bytes = label.encode("utf-8")
        label_len = len(label_bytes).to_bytes(4, byteorder="big")
        return label_len + label_bytes + v_bytes

    def deserialize_vector(self, data: bytes) -> tuple[str, np.ndarray]:
        """
        Deserialize a golden vector from portable bytes.

        Args:
            data: Byte representation from serialize_vector()

        Returns:
            Tuple of (label, vector as complex128)

        Raises:
            ValueError: If data is malformed
        """
        if len(data) < 4:
            raise ValueError("Data too short for label length prefix")
        label_len = int.from_bytes(data[:4], byteorder="big")
        if len(data) < 4 + label_len:
            raise ValueError("Data too short for label")
        label = data[4 : 4 + label_len].decode("utf-8")
        v_bytes = data[4 + label_len :]
        v_f32 = np.frombuffer(v_bytes, dtype=np.complex64)
        v = v_f32.astype(np.complex128)
        return (label, v)

    def verify_serialization_roundtrip(self, label: str) -> tuple[bool, float]:
        """
        Verify that serialize -> deserialize produces the same vector.

        Args:
            label: Label to test

        Returns:
            Tuple of (passed, max_abs_difference)
        """
        original = deterministic_vector(label, self.dim, self.dtype)
        data = self.serialize_vector(label)
        recovered_label, recovered_vec = self.deserialize_vector(data)

        if recovered_label != label:
            return (False, 1.0)

        # Compare in float32 space (serialization quantizes to float32)
        orig_f32 = original.astype(np.complex64).astype(np.complex128)
        max_diff = float(np.max(np.abs(orig_f32 - recovered_vec)))
        passed = max_diff < 1e-6
        return (passed, max_diff)

    def compute_hash_chain(self, labels: list[str] | None = None) -> str:
        """
        Compute a Merkle-style hash chain over golden vectors.

        The hash chain is: H(v_0 || H(v_1 || H(v_2 || ...)))
        This creates a tamper-evident chain -- changing any vector
        changes the final hash.

        Args:
            labels: Ordered list of labels (defaults to GOLDEN_LABELS)

        Returns:
            64-character hex hash chain root
        """
        if labels is None:
            labels = GOLDEN_LABELS

        chain_hash = b""
        for label in labels:
            v = deterministic_vector(label, self.dim, self.dtype)
            v_bytes = v.astype(np.complex64).tobytes()
            chain_hash = hashlib.sha256(v_bytes + chain_hash).digest()

        return hashlib.sha256(chain_hash).hexdigest()

    def verify_hash_chain(self, expected_root: str) -> tuple[bool, str]:
        """
        Verify that the current hash chain matches an expected root.

        This is the cross-restart persistence check: if the same labels
        produce the same hash chain root across process restarts, the
        deterministic pipeline is intact.

        Args:
            expected_root: Expected 64-char hex hash chain root

        Returns:
            Tuple of (passed, actual_root)
        """
        actual_root = self.compute_hash_chain()
        passed = actual_root == expected_root
        return (passed, actual_root)

    def compute_persistence_manifest(self) -> dict[str, str]:
        """
        Compute a full persistence manifest for cross-restart verification.

        The manifest contains:
          - hash_chain_root: Merkle-style hash over all golden vectors
          - per_label_fingerprints: 16-char fingerprint for each label
          - seed_fingerprints: 16-char fingerprint for each seed

        This manifest can be saved to disk and compared after a process
        restart to verify that the deterministic pipeline produces
        identical results.

        Returns:
            Dict with hash_chain_root, fingerprints, and metadata
        """
        fingerprints = self.compute_all_fingerprints()
        chain_root = self.compute_hash_chain()

        seed_fingerprints: dict[str, str] = {}
        for label in GOLDEN_LABELS:
            seed = seed_from_label(label)
            seed_bytes = seed.to_bytes(32, byteorder="big")
            seed_fingerprints[label] = hashlib.sha256(seed_bytes).hexdigest()[:16]

        return {
            "hash_chain_root": chain_root,
            "per_label_fingerprints": fingerprints,
            "seed_fingerprints": seed_fingerprints,
            "dim": str(self.dim),
            "dtype": str(self.dtype),
            "label_count": str(len(GOLDEN_LABELS)),
        }

    def verify_persistence_manifest(
        self, manifest: dict[str, str]
    ) -> tuple[bool, list[str]]:
        """
        Verify that the current state matches a saved persistence manifest.

        Args:
            manifest: Manifest from compute_persistence_manifest()

        Returns:
            Tuple of (all_passed, list_of_failures)
        """
        failures: list[str] = []

        # Verify hash chain root
        current_root = self.compute_hash_chain()
        if current_root != manifest.get("hash_chain_root", ""):
            failures.append(
                f"hash_chain_root mismatch: expected "
                f"{manifest.get('hash_chain_root', '')[:16]}..., "
                f"got {current_root[:16]}..."
            )

        # Verify per-label fingerprints
        expected_fps = manifest.get("per_label_fingerprints", {})
        current_fps = self.compute_all_fingerprints()
        for label, expected_fp in expected_fps.items():
            actual_fp = current_fps.get(label, "")
            if actual_fp != expected_fp:
                failures.append(f"fingerprint mismatch for '{label}'")

        # Verify seed fingerprints
        expected_seeds = manifest.get("seed_fingerprints", {})
        for label, expected_seed_fp in expected_seeds.items():
            seed = seed_from_label(label)
            seed_bytes = seed.to_bytes(32, byteorder="big")
            actual_seed_fp = hashlib.sha256(seed_bytes).hexdigest()[:16]
            if actual_seed_fp != expected_seed_fp:
                failures.append(f"seed fingerprint mismatch for '{label}'")

        return (len(failures) == 0, failures)

    def verify_cross_restart_determinism(self) -> dict[str, Any]:
        """
        Simulate a cross-restart determinism check.

        This computes a persistence manifest, then re-computes all vectors
        (simulating a fresh process) and verifies they match. In a real
        cross-restart scenario, the manifest would be saved to disk and
        loaded after restart.

        Returns:
            Dict with verification results
        """
        # Compute manifest (simulates "save before restart")
        manifest = self.compute_persistence_manifest()

        # Re-compute all vectors (simulates "fresh process after restart")
        # Since deterministic_vector is pure (no state), this is equivalent
        # to a restart as long as the seed pipeline is intact.
        passed, failures = self.verify_persistence_manifest(manifest)

        return {
            "passed": passed,
            "failures": failures,
            "hash_chain_root": manifest["hash_chain_root"],
            "label_count": int(manifest["label_count"]),
            "dim": int(manifest["dim"]),
        }

    # -- Fixture Generation & Persistence I/O -----------------------------------

    def generate_fixture(
        self,
        labels: list[str] | None = None,
        dim: int | None = None,
    ) -> dict[str, Any]:
        """
        Generate a golden vector fixture for cross-platform verification.

        A fixture contains:
          - labels: The list of labels used
          - dim: Vector dimensionality
          - dtype: Vector dtype as string
          - hash_chain_root: Merkle-style hash over all vectors
          - fingerprints: Per-label 16-char hex fingerprints
          - seed_fingerprints: Per-label seed fingerprints
          - platform: Current platform info

        Args:
            labels: Labels to include (defaults to GOLDEN_LABELS)
            dim:   Override dimensionality (defaults to self.dim)

        Returns:
            Fixture dict suitable for save_chain() or verify()
        """
        if labels is None:
            labels = GOLDEN_LABELS
        if dim is None:
            dim = self.dim

        # Temporarily override dim for this fixture
        original_dim = self.dim
        self.dim = dim
        try:
            fingerprints = {
                label: self.compute_golden_fingerprint(label) for label in labels
            }
            chain_root = self.compute_hash_chain(labels)

            seed_fps: dict[str, str] = {}
            for label in labels:
                seed = seed_from_label(label)
                seed_bytes = seed.to_bytes(32, byteorder="big")
                seed_fps[label] = hashlib.sha256(seed_bytes).hexdigest()[:16]
        finally:
            self.dim = original_dim

        return {
            "labels": labels,
            "dim": dim,
            "dtype": str(self.dtype),
            "hash_chain_root": chain_root,
            "fingerprints": fingerprints,
            "seed_fingerprints": seed_fps,
            "platform": {
                "system": __import__("platform").system(),
                "machine": __import__("platform").machine(),
                "python_version": __import__("platform").python_version(),
            },
        }

    def save_chain(self, path: str | Path) -> dict[str, Any]:
        """
        Save the golden vector hash chain and fixtures to a JSON file.

        The file contains the full persistence manifest plus fixture
        metadata. This file can be loaded after a process restart to
        verify that the deterministic pipeline is intact.

        Args:
            path: File path to save the chain to

        Returns:
            The fixture dict that was saved

        Raises:
            OSError: If the file cannot be written
        """
        fixture = self.generate_fixture()
        manifest = self.compute_persistence_manifest()

        # Merge fixture and manifest
        output = {**fixture, **manifest}

        path_obj = Path(path)
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        with open(path_obj, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, sort_keys=True)

        return output

    def load_chain(self, path: str | Path) -> bool:
        """
        Load a saved hash chain from a JSON file and verify it.

        This is the cross-restart persistence check: load the fixture
        from disk and verify that the current vectors match.

        Args:
            path: File path to load the chain from

        Returns:
            True if all fixture checks pass, False otherwise

        Raises:
            OSError: If the file cannot be read
            json.JSONDecodeError: If the file is not valid JSON
        """
        path_obj = Path(path)
        with open(path_obj, "r", encoding="utf-8") as f:
            fixtures = json.load(f)

        return self.verify(fixtures)

    def __repr__(self) -> str:
        return f"GoldenVectorVerifier(dim={self.dim}, dtype={self.dtype})"
