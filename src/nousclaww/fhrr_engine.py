"""
FHRR Engine -- 1,024-D Complex Fourier Holographic Reduced Representations.

#C Adapted from NoUs-fordge Nous-hub mvp_local_core core/fhrr_engine.py

The working memory tier of the three-tier VSA funnel. Operates on complex
unit-circle vectors (e^{i*theta}) with element-wise multiplication for binding
and conjugate multiplication for unbinding.

CRITICAL ARCHITECTURAL RULES (per Caterpillar red-team):
  1. Every bind and bundle operation forces unit-circle normalization
     to prevent recursive FFT drift from compounding
  2. Every unbind output MUST pass through clean-up memory (ArgMax)
     before downstream consumption -- unbinding yields noise, not signal
  3. The codebook is INJECTED as an explicit parameter, never stored
     as module state (Socket Pattern)
  4. Hard boundary typing: all inputs validated before operations

Socket Pattern: This module imports ONLY numpy, typing, and hrr_math.
No UI, routing, or framework imports.

SYNTH:
    purpose: 1024-D complex FHRR working memory -- bind/unbind/bundle operations with mandatory unit-circle normalization and clean-up memory integration.
    axioms: [local_first, scientific_method, evidence_over_intuition, honest_failure_over_fake_success]
    objective: Lossless bind/unbind round-trips for single bindings, and accurate ArgMax retrieval from bundles of up to D/10 items via clean-up memory.
    anti_patterns:
        - Never skip unit-circle normalization after bind or bundle (recursive FFT drift compounds and shatters vectors)
        - Never consume raw unbind output without clean-up memory (yields noise, not signal -- 0% accuracy without clean-up)
        - Never store the codebook as module state (Socket Pattern -- math modules don't know the backend)
        - Never skip input dimension validation (silent corruption from malformed inputs)
        - Never use weights that are not clamped to [0, 1] and normalized (cognitive gravity requires bounded influence)
"""

from __future__ import annotations

import numpy as np

from .cleanup_memory import CleanupCodebook, InMemoryCodebook
from .hrr_math import (
    WORKING_DIM,
    _normalize_unit_circle,
    bind as _bind,
    unbind as _unbind,
    cosine_similarity,
    deterministic_vector,
    validate_dimension,
)


class FHRR:
    """
    1,024-D complex FHRR engine for working memory operations.

    Provides:
      - generate(label): Deterministic base vector from label string
      - bind(key, value): Associate two vectors (element-wise multiply)
      - unbind(bound, key): Recover value from binding (conjugate multiply)
      - bundle(vectors, weights): Superpose multiple vectors into one trace
      - cleanup(vector, codebook): ArgMax clean-up lookup against a codebook
      - similarity(a, b): Cosine similarity (Hermitian inner product)

    All bind and bundle operations enforce unit-circle normalization to
    prevent recursive FFT drift in multi-level binding chains.

    Attributes:
        dimensions: Vector dimensionality (default 1024)
    """

    def __init__(self, dimensions: int = WORKING_DIM) -> None:
        """
        Initialize the FHRR engine.

        Args:
            dimensions: Vector dimensionality (default 1024, must be
                       positive; power of 2 recommended for FFT performance)

        Raises:
            ValueError: If dimensions is not positive
        """
        if dimensions <= 0:
            raise ValueError(f"dimensions must be positive, got {dimensions}")
        self.dimensions = dimensions

    def generate(self, label: str) -> np.ndarray:
        """
        Generate a deterministic base vector from a label string.

        Uses the full 256-bit SHA-256 seed -> PCG64 -> uint64 genesis ->
        float32 quantization pipeline from hrr_math. Same label always
        produces the same vector on every platform.

        Args:
            label: Opaque label string (e.g., "vctx::0", "sensor::heart_rate")

        Returns:
            Complex128 unit-circle vector of shape (dimensions,)
        """
        return deterministic_vector(label, self.dimensions, np.complex128)

    def bind(self, key: np.ndarray, value: np.ndarray) -> np.ndarray:
        """
        Bind a key and value via element-wise complex multiplication.

        The result is FORCED back onto the unit circle to prevent
        recursive FFT drift in multi-level binding chains.

        Args:
            key:   Key vector, shape (dimensions,), complex
            value: Value vector, shape (dimensions,), complex

        Returns:
            Bound vector on unit circle, shape (dimensions,), complex

        Raises:
            ValueError: If either vector has wrong dimensionality
            TypeError: If either vector is not a numpy array
        """
        validate_dimension(key, self.dimensions, "key")
        validate_dimension(value, self.dimensions, "value")
        return _bind(key, value)  # _bind normalizes for complex

    def unbind(
        self,
        bound: np.ndarray,
        key: np.ndarray,
        codebook: CleanupCodebook | None = None,
    ) -> tuple[np.ndarray, str, float] | np.ndarray:
        """
        Unbind a value from a binding -- the MANDATORY retrieval pipeline.

        When codebook is provided (the normal case):
          Step 1: Unbind (conjugate multiply) -> noisy approximation
          Step 2: Clean-up (ArgMax cosine similarity against codebook)
          Step 3: Return (clean_vector, label, similarity_score)

        When codebook is None (debugging/testing only):
          Returns the raw noisy approximation WITHOUT clean-up.
          This is only useful for inspecting the noise floor. Never
          use the raw output for downstream memory operations.

        Args:
            bound:    Bound or bundled vector, shape (dimensions,), complex
            key:      Key vector to unbind with, shape (dimensions,), complex
            codebook: Injectable clean-up memory (CleanupCodebook protocol).
                      MANDATORY for production use. Only omit for debugging.

        Returns:
            With codebook: Tuple of (clean_vector, label, similarity_score)
            Without codebook: Raw noisy np.ndarray (debugging only)

        Raises:
            ValueError: If vectors have wrong dimensionality
            TypeError: If vectors are not numpy arrays
        """
        validate_dimension(bound, self.dimensions, "bound")
        validate_dimension(key, self.dimensions, "key")
        noisy = _unbind(key, bound)  # Does NOT normalize -- noise is meaningful

        if codebook is None:
            # Debugging path -- raw noisy output, no clean-up
            return noisy
        # Production path -- mandatory clean-up lookup
        return codebook.lookup(noisy)

    def bundle(
        self,
        vectors: list[np.ndarray],
        weights: list[float] | None = None,
    ) -> np.ndarray:
        """
        Superpose multiple vectors into a single memory trace.

        WEIGHTED SUPERPOSITION (Cognitive Gravity):
          When weights are provided, performs weighted sum: alpha*A + beta*B + ...
          This implements cognitive gravity -- older or less relevant
          engrams decay via lower weights, while salient memories
          dominate the bundle. Weights are clamped to [0, 1] and
          normalized to sum to 1 before application.

          Example: bundle([old_mem, recent_mem], weights=[0.3, 0.7])
          -> recent_mem contributes 70% of the signal, old_mem 30%

        POST-BUNDLE NORMALIZATION (Mandatory):
          After summation (weighted or flat), the result is forced
          back onto the complex unit circle via v / |v|. This prevents
          magnitude explosion in recursive binding chains and preserves
          phase angle precision required for unbinding.

        Args:
            vectors: List of vectors, each shape (dimensions,), complex
            weights: Optional list of scalar weights (cognitive gravity).
                     If None, equal weighting (flat sum). If provided,
                     must have same length as vectors. Weights are
                     clamped to [0, 1] and normalized to sum to 1.

        Returns:
            Bundled vector on unit circle, shape (dimensions,), complex

        Raises:
            ValueError: If vectors is empty, shapes mismatch, or
                       weights length != vectors length
        """
        if not vectors:
            raise ValueError("bundle requires at least one vector")
        for i, v in enumerate(vectors):
            validate_dimension(v, self.dimensions, f"vector[{i}]")

        vecs = [np.asarray(v, dtype=np.complex128) for v in vectors]

        if weights is not None:
            if len(weights) != len(vectors):
                raise ValueError(
                    f"weights length ({len(weights)}) must match "
                    f"vectors length ({len(vectors)})"
                )
            # Clamp weights to [0, 1] and normalize to sum to 1
            w = np.array(weights, dtype=np.float64)
            w = np.clip(w, 0.0, 1.0)
            total = w.sum()
            if total < 1e-12:
                # All weights zero -- fall back to equal weighting
                w = np.ones(len(vectors)) / len(vectors)
            else:
                w = w / total

            # Weighted superposition: sum(w_i * v_i)
            result = np.zeros(self.dimensions, dtype=np.complex128)
            for wi, vi in zip(w, vecs):
                result += wi * vi
        else:
            # Equal weighting (flat sum)
            result = np.sum(vecs, axis=0)

        # MANDATORY: Post-bundle unit-circle normalization
        # This prevents magnitude explosion and preserves phase precision
        return _normalize_unit_circle(result)

    def cleanup(
        self,
        vector: np.ndarray,
        codebook: CleanupCodebook,
    ) -> tuple[np.ndarray, str, float]:
        """
        Clean-up memory lookup -- find the best matching codebook entry.

        This is the MANDATORY step after unbinding. Unbinding yields a
        noisy approximation; clean-up memory recovers the pristine vector
        via ArgMax cosine similarity against a codebook of known vectors.

        Args:
            vector:   Noisy vector from unbind, shape (dimensions,), complex
            codebook: Injectable clean-up memory (CleanupCodebook protocol)

        Returns:
            Tuple of (clean_vector, label, similarity_score)
            - clean_vector: Best matching vector from codebook (copy)
            - label: Opaque label string of the best match
            - similarity_score: Cosine similarity in [-1, 1]

        Raises:
            ValueError: If vector has wrong dimensionality or codebook is empty
            TypeError: If vector is not a numpy array
        """
        validate_dimension(vector, self.dimensions, "vector")
        return codebook.lookup(vector)

    def similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """
        Cosine similarity between two FHRR vectors.

        Uses the Hermitian inner product: <a, b> = sum(conj(a_i) * b_i),
        normalized by L2 norms. Returns value in [-1, 1].

        Args:
            a, b: Vectors of shape (dimensions,), complex

        Returns:
            Cosine similarity in [-1, 1]

        Raises:
            ValueError: If vectors have wrong dimensionality
            TypeError: If vectors are not numpy arrays
        """
        validate_dimension(a, self.dimensions, "a")
        validate_dimension(b, self.dimensions, "b")
        return cosine_similarity(a, b)

    def normalize(self, v: np.ndarray) -> np.ndarray:
        """
        Force a complex vector back onto the unit circle.

        Divides each component by its magnitude. This is done
        automatically by bind() and bundle(), but can be called
        manually if needed.

        Args:
            v: Complex vector, shape (dimensions,)

        Returns:
            Unit-circle-normalized vector

        Raises:
            ValueError: If vector has wrong dimensionality
            TypeError: If vector is not a numpy array
        """
        validate_dimension(v, self.dimensions, "v")
        return _normalize_unit_circle(v)

    def create_codebook(self) -> InMemoryCodebook:
        """
        Create a new empty in-memory codebook for this engine's dimensionality.

        Convenience method. The codebook is returned empty -- the caller
        is responsible for populating it with base vectors.

        Returns:
            Empty InMemoryCodebook with dim = self.dimensions
        """
        return InMemoryCodebook(dim=self.dimensions)

    def __repr__(self) -> str:
        return f"FHRR(dimensions={self.dimensions})"
