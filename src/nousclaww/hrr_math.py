"""
HRR Math Core -- Cross-Architecture Deterministic Vector Symbolic Architecture.

#C Adapted from NoUs-fordge Nous-hub mvp_local_core core/hrr_math.py
Academic basis: Plate (1995, 2003) "Holographic Reduced Representations".

CRITICAL ARCHITECTURAL DECISIONS (per Caterpillar red-team):
  1. Full 256-bit SHA-256 seed -- no truncation (32-bit truncation causes
     birthday paradox collision at ~65k labels, destroying VSA orthogonality)
  2. Integer Genesis -- raw uint64 from PCG64, NOT rng.uniform() or
     rng.standard_normal(), to bypass hardware-specific libm drift
  3. Float32 Quantization -- complex vectors quantized to float32 precision
     to sever the non-deterministic tail where x86 vs ARM FMA instructions
     diverge (~1e-15 in float64). float32 has ~7 decimal digits, chopping
     all cross-architecture drift.

Socket Pattern: This module imports ONLY numpy and typing. No UI, routing,
or framework imports. Pure vector-in, vector-out.

SYNTH:
    purpose: Foundation VSA math -- deterministic vector generation, binding, bundling, and similarity via PCG64 RNG and IEEE 754 deterministic casting.
    axioms: [local_first, scientific_method, evidence_over_intuition, iteration_is_progress]
    objective: Bit-for-bit identical hypervectors on every platform (x86, ARM, Snapdragon NPU) for any given label+dim+dtype triple.
    anti_patterns:
        - Never truncate the SHA-256 seed to 32 bits (birthday paradox at 65k labels)
        - Never use rng.uniform() or rng.standard_normal() for vector generation (libm drift)
        - Never skip unit-circle normalization after bind/bundle (recursive FFT drift compounds)
        - Never import UI, routing, or framework modules (Socket Pattern violation)
        - Never skip float32 quantization (cross-architecture drift survives)
"""

from __future__ import annotations

import hashlib
import warnings

import numpy as np

# -- Dimensional constants (hard boundary typing) ---------------------------------

ROUTING_DIM = 10_000   # Tier 1: XOR-HDC binary routing space
WORKING_DIM = 1_024    # Tier 2: FHRR complex working memory
STORAGE_DIM = 32       # Tier 3: FHRR folded long-term storage

# Quantization precision: float32 has ~7 decimal digits.
# Cross-architecture libm drift lives at ~1e-15 (float64 tail).
# Quantizing to float32 chops all drift, ensuring bit-for-bit identical
# vectors on x86, ARM, Snapdragon NPU, and any future silicon.
_QUANTIZE_DTYPE = np.float32


# -- Deterministic Seeding --------------------------------------------------------


def seed_from_label(label: str) -> int:
    """
    Derive a deterministic RNG seed from a label string via SHA-256.

    Uses the FULL 256-bit hash -- never truncate. A 32-bit truncation
    introduces a birthday paradox collision at ~65,536 labels, which
    destroys VSA orthogonality by mapping distinct labels to identical
    base vectors. With 256 bits, collision probability is below cosmic
    ray bit-flip probability.

    Args:
        label: Opaque label string (e.g., "vctx::0", "sensor::heart_rate")

    Returns:
        Full 256-bit integer suitable for np.random.default_rng()
    """
    digest = hashlib.sha256(label.encode("utf-8")).digest()
    return int.from_bytes(digest, byteorder="big", signed=False)


# -- Deterministic Vector Generation (Integer Genesis + Quantization) -------------


def deterministic_vector(
    label: str,
    dim: int,
    dtype: np.dtype = np.complex128,
    unitary: bool = False,
) -> np.ndarray:
    """
    Generate a deterministic vector from a label string.

    Pipeline (cross-architecture safe):
      1. seed_from_label(label) -> 256-bit integer
      2. PCG64 RNG seeded with 256-bit integer -> raw uint64 stream
      3. Integer Genesis: map uint64 -> [0, 2*pi) via strict IEEE 754 division
      4. Compute exp(i*theta) = cos(theta) + i*sin(theta) in float64
      5. Quantize to float32 to sever non-deterministic libm tail
      6. Promote back to target dtype

    For real-valued (HRR) vectors with unitary=True:
      - After generation, FFT magnitudes are normalized to 1
      - This ensures lossless bind/unbind round-trip via FFT convolution
      - Required for KEY vectors in HRR binding

    For complex (FHRR) vectors:
      - All vectors are inherently unitary (|e^{i*theta}| = 1)
      - unitary parameter has no effect

    Same label + same dim + same dtype = identical vector on EVERY platform.
    x86, ARM, Snapdragon NPU -- bit-for-bit identical after quantization.

    Args:
        label:   Opaque label string
        dim:     Hypervector dimensionality
        dtype:   Target dtype (complex128 for FHRR, float64 for HRR)
        unitary: If True and dtype is real, normalize FFT magnitudes to 1

    Returns:
        Deterministic hypervector of shape (dim,)

    Raises:
        ValueError: If dim is not positive
        TypeError: If dtype is not supported
    """
    if dim <= 0:
        raise ValueError(f"dim must be positive, got {dim}")

    seed = seed_from_label(label)
    rng = np.random.default_rng(seed)

    if dtype == np.complex128 or dtype == np.complex64:
        # -- FHRR: Complex unit-circle vector --------------------------------
        # Step 1: Integer Genesis -- raw uint64 from PCG64
        raw_uint64 = rng.integers(
            low=0,
            high=np.iinfo(np.uint64).max,
            size=dim,
            dtype=np.uint64,
        )

        # Step 2: Deterministic float casting via strict IEEE 754 division
        # theta = 2*pi * (raw_uint64 / (2^64 - 1))
        # np.pi is a hardcoded constant (same bits everywhere)
        # uint64 -> float64 conversion is IEEE 754 deterministic
        # float64 division is IEEE 754 deterministic
        scale = np.float64(2.0) * np.float64(np.pi)
        divisor = np.float64(np.iinfo(np.uint64).max)  # 2^64 - 1
        phases = raw_uint64.astype(np.float64) * (scale / divisor)

        # Step 3: Compute complex exponential (libm -- non-deterministic tail)
        raw_complex = np.exp(1j * phases)

        # Step 4: Quantize to float32 to sever non-deterministic tail
        # float32 has ~7 decimal digits. libm drift lives at ~1e-15.
        # Casting to float32 chops all drift bits.
        real_q = raw_complex.real.astype(_QUANTIZE_DTYPE)
        imag_q = raw_complex.imag.astype(_QUANTIZE_DTYPE)

        # Step 5: Promote back to target dtype
        result = real_q.astype(np.float64) + 1j * imag_q.astype(np.float64)
        return result.astype(dtype)

    elif dtype == np.float64 or dtype == np.float32:
        # -- HRR: Real-valued vector -----------------------------------------
        # Step 1: Integer Genesis -- raw uint64 from PCG64
        raw_uint64 = rng.integers(
            low=0,
            high=np.iinfo(np.uint64).max,
            size=dim,
            dtype=np.uint64,
        )

        # Step 2: Map uint64 -> [-1, 1] via deterministic IEEE 754 division
        _divisor = np.float64(np.iinfo(np.uint64).max)  # 2^64 - 1
        normalized = (raw_uint64.astype(np.float64) / _divisor) * 2.0 - 1.0

        # Step 3: Quantize to float32
        quantized = normalized.astype(_QUANTIZE_DTYPE)

        # Step 4: L2-normalize (deterministic after quantization)
        result = quantized.astype(np.float64)
        norm = np.linalg.norm(result)
        if norm > 1e-12:
            result = result / norm

        # Step 5: If unitary requested, normalize FFT magnitudes to 1
        # This ensures lossless bind/unbind round-trip via FFT convolution
        if unitary:
            v_f = np.fft.fft(result)
            v_f = v_f / (np.abs(v_f) + 1e-12)
            result = np.fft.ifft(v_f).real
            # Re-quantize after IFFT to maintain float32 precision
            result = result.astype(_QUANTIZE_DTYPE).astype(np.float64)

        return result.astype(dtype)

    else:
        raise TypeError(
            f"Unsupported dtype: {dtype}. "
            "Use complex128, complex64, float64, or float32."
        )


def make_unitary(
    dim: int = WORKING_DIM,
    seed: int | None = None,
) -> np.ndarray:
    """
    Generate a unitary vector for HRR binding.

    A unitary vector has the property that binding with it is lossless:
    unbind(bind(x, u), u) == x (to within float precision).

    For FHRR (complex): all vectors are inherently unitary because
    |e^{i*theta}| = 1 for all components. This function is provided for
    HRR (real-valued) compatibility.

    Args:
        dim:  Hypervector dimensionality
        seed: Optional RNG seed (use label-based generation for determinism)

    Returns:
        Unitary hypervector of shape (dim,)
    """
    if seed is not None:
        rng = np.random.default_rng(seed)
        raw = rng.integers(
            low=0,
            high=np.iinfo(np.uint64).max,
            size=dim,
            dtype=np.uint64,
        )
        divisor = np.float64(np.iinfo(np.uint64).max)
        v = (raw.astype(np.float64) / divisor) * 2.0 - 1.0
        v = v.astype(_QUANTIZE_DTYPE).astype(np.float64)
    else:
        warnings.warn(
            "make_unitary() called without seed -- non-deterministic. "
            "Pass seed=label_hash for determinism.",
            DeprecationWarning,
            stacklevel=2,
        )
        v = np.random.standard_normal(dim)

    # Make unitary in frequency domain: normalize magnitudes to 1
    v_f = np.fft.fft(v)
    v_f = v_f / (np.abs(v_f) + 1e-12)
    result = np.fft.ifft(v_f).real

    # Quantize
    result = result.astype(_QUANTIZE_DTYPE).astype(np.float64)
    return result


# -- HRR Binding Operations (FFT-based) -------------------------------------------


def _normalize_unit_circle(v: np.ndarray) -> np.ndarray:
    """
    Force a complex vector back onto the unit circle.

    Divides each component by its magnitude: v_i -> v_i / |v_i|.
    Components with near-zero magnitude are left unchanged (guard).

    This is MANDATORY after every binding and bundling operation to
    prevent recursive FFT drift from compounding across multi-level
    binding chains. Without this, phase errors multiply through
    recursive bind(bind(bind(a, b), c), d) chains and eventually
    shatter the ArgMax clean-up lookup.

    Args:
        v: Complex vector

    Returns:
        Unit-circle-normalized complex vector (same shape, same dtype)
    """
    if not np.iscomplexobj(v):
        return v
    magnitudes = np.abs(v)
    # Guard: avoid division by zero for near-zero components
    safe_mags = np.where(magnitudes < 1e-12, 1.0, magnitudes)
    return (v / safe_mags).astype(v.dtype)


def bind(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """
    Binding operation -- circular convolution for HRR, element-wise multiply for FHRR.

    For REAL vectors (HRR): a * b = IFFT(FFT(a) (.) FFT(b))
      - Circular convolution via FFT
      - Used for real-valued hypervectors (10,000-D sensor tier)
      - Result is L2-normalized to prevent magnitude drift

    For COMPLEX vectors (FHRR): a * b = a (.) b (element-wise)
      - Element-wise complex multiplication
      - Used for complex unit-circle vectors (1,024-D working memory)
      - Result is FORCED back onto unit circle to prevent recursive FFT drift

    Properties (both modes):
      - Commutative: bind(a, b) == bind(b, a)
      - Associative: bind(bind(a, b), c) == bind(a, bind(b, c))
      - Creates a new vector jointly encoding a and b

    Args:
        a, b: Vectors of identical shape (real or complex)

    Returns:
        Bound vector of same shape and dtype as input

    Raises:
        ValueError: If shapes mismatch or vectors are not 1-D
    """
    vec_a = np.asarray(a)
    vec_b = np.asarray(b)
    if vec_a.shape != vec_b.shape:
        raise ValueError(
            f"bind requires identical shapes, got {vec_a.shape} vs {vec_b.shape}"
        )
    if vec_a.ndim != 1:
        raise ValueError(f"bind requires 1-D vectors, got {vec_a.ndim}-D")

    if np.iscomplexobj(vec_a) or np.iscomplexobj(vec_b):
        # FHRR: element-wise complex multiplication
        result = vec_a * vec_b
        # MANDATORY: Force back onto unit circle to prevent recursive drift
        return _normalize_unit_circle(result)
    else:
        # HRR: FFT-based circular convolution
        result = np.fft.ifft(np.fft.fft(vec_a) * np.fft.fft(vec_b))
        result = result.real.astype(vec_a.dtype)
        # L2-normalize to prevent magnitude drift in recursive chains
        norm = np.linalg.norm(result)
        if norm > 1e-12:
            result = result / norm
        return result


def unbind(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """
    Unbinding operation -- circular correlation for HRR, conjugate multiply for FHRR.

    For REAL vectors (HRR): a # b = IFFT(conj(FFT(a)) (.) FFT(b))
      - Circular correlation via FFT
      - Approximate inverse of circular convolution

    For COMPLEX vectors (FHRR): a # b = b (.) conj(a) (element-wise)
      - Element-wise multiply by conjugate of key
      - Perfect inverse when key has unit magnitude: |a_i| = 1
      - unbind(bind(key, val), key) = val * conj(key) * key = val * |key|^2 = val

    Note: Unbind output is NOT unit-circle normalized. The output is a
    noisy approximation that will be passed to clean-up memory. The
    clean-up lookup uses cosine similarity which is magnitude-invariant,
    so normalization here would discard useful magnitude information.

    CRITICAL: The result is a NOISY APPROXIMATION when unbinding from
    a bundle (superposition of multiple bindings). Superposition noise
    scales as sqrt(N) where N = number of bound items. The output MUST be
    passed through an associative clean-up memory (ArgMax cosine
    similarity against a codebook) for accurate recovery.

    Args:
        a: Key vector (used for unbinding)
        b: Bound or bundled vector to unbind from

    Returns:
        Noisy approximation of the original value vector

    Raises:
        ValueError: If shapes mismatch or vectors are not 1-D
    """
    vec_a = np.asarray(a)
    vec_b = np.asarray(b)
    if vec_a.shape != vec_b.shape:
        raise ValueError(
            f"unbind requires identical shapes, got {vec_a.shape} vs {vec_b.shape}"
        )
    if vec_a.ndim != 1:
        raise ValueError(f"unbind requires 1-D vectors, got {vec_a.ndim}-D")

    if np.iscomplexobj(vec_a) or np.iscomplexobj(vec_b):
        # FHRR: element-wise multiply by conjugate of key
        # Do NOT normalize -- output is noisy, clean-up handles it
        return vec_b * np.conj(vec_a)
    else:
        # HRR: FFT-based circular correlation
        a_f = np.fft.fft(vec_a)
        b_f = np.fft.fft(vec_b)
        result = np.fft.ifft(np.conj(a_f) * b_f)
        return result.real.astype(vec_a.dtype)


# Alias for compatibility with source naming
circ_conv = bind
circ_corr = unbind


def bundle(vectors: list[np.ndarray]) -> np.ndarray:
    """
    Superposition (bundling) -- sum multiple vectors into a single trace.

    For COMPLEX vectors (FHRR):
      - Element-wise sum, then unit-circle normalization
      - MANDATORY normalization prevents magnitude explosion and
        recursive FFT drift in multi-level binding chains

    For REAL vectors (HRR):
      - Element-wise sum, then L2 normalization
      - Prevents magnitude drift

    The bundled vector is similar (cosine sim > 0.1) to each input --
    this is the holographic property of VSA: the whole contains
    traces of all parts.

    Args:
        vectors: List of vectors of identical shape

    Returns:
        Bundled vector of same shape and dtype as inputs

    Raises:
        ValueError: If list is empty or shapes mismatch
    """
    if not vectors:
        raise ValueError("bundle requires at least one vector")

    vecs = [np.asarray(v) for v in vectors]
    first_shape = vecs[0].shape
    for i, v in enumerate(vecs):
        if v.shape != first_shape:
            raise ValueError(
                f"bundle requires identical shapes, vector 0 has {first_shape}, "
                f"vector {i} has {v.shape}"
            )

    # Sum all vectors
    result = np.sum(vecs, axis=0)

    if np.iscomplexobj(result):
        # FHRR: force back onto unit circle
        return _normalize_unit_circle(result)
    else:
        # HRR: L2 normalize
        norm = np.linalg.norm(result)
        if norm > 1e-12:
            result = result / norm
        return result


# -- Similarity Metrics -----------------------------------------------------------


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """
    Cosine similarity between two vectors with zero-norm guard.

    For complex vectors: uses the Hermitian inner product
    <a, b> = sum(conj(a_i) * b_i), normalized by L2 norms.

    Returns value in [-1, 1]:
      - 1.0 = identical
      - 0.0 = orthogonal
      - -1.0 = opposite

    Args:
        a, b: Vectors to compare

    Returns:
        Cosine similarity in [-1, 1]

    Raises:
        ValueError: If shapes mismatch
    """
    vec_a = np.asarray(a).ravel()
    vec_b = np.asarray(b).ravel()
    if vec_a.shape != vec_b.shape:
        raise ValueError(
            f"cosine_similarity requires identical shapes, "
            f"got {vec_a.shape} vs {vec_b.shape}"
        )

    if np.iscomplexobj(vec_a) or np.iscomplexobj(vec_b):
        # Hermitian inner product for complex vectors
        dot = float(np.real(np.vdot(vec_a, vec_b)))
    else:
        dot = float(np.dot(vec_a, vec_b))

    norm_a = float(np.linalg.norm(vec_a))
    norm_b = float(np.linalg.norm(vec_b))

    if norm_a < 1e-12 or norm_b < 1e-12:
        return 0.0

    return dot / (norm_a * norm_b)


def normalize(v: np.ndarray) -> np.ndarray:
    """
    L2-normalize a vector. Returns zero vector if input norm < epsilon.

    For complex vectors, normalizes the magnitude while preserving phase.

    Args:
        v: Vector to normalize

    Returns:
        L2-normalized vector, or zero vector if norm < epsilon
    """
    vec = np.asarray(v)
    norm = np.linalg.norm(vec)
    if norm < 1e-12:
        return np.zeros_like(vec)
    return vec / norm


# -- Shape Validation (Hard Boundary Typing) --------------------------------------


def validate_dimension(v: np.ndarray, expected_dim: int, name: str = "vector") -> None:
    """
    Validate that a vector has the expected dimensionality.

    Raises ValueError or TypeError immediately if the shape or dtype
    is wrong. This prevents silent corruption from malformed inputs
    entering matrix operations.

    Args:
        v:             Vector to validate
        expected_dim:  Expected dimensionality (e.g., 1024, 10000, 32)
        name:          Human-readable name for error messages

    Raises:
        TypeError:  If v is not a numpy array
        ValueError: If v is not 1-D or has wrong dimensionality
    """
    if not isinstance(v, np.ndarray):
        raise TypeError(f"{name} must be np.ndarray, got {type(v)}")
    if v.ndim != 1:
        raise ValueError(
            f"{name} must be 1-D, got {v.ndim}-D with shape {v.shape}"
        )
    if v.shape[0] != expected_dim:
        raise ValueError(
            f"{name} must have dimension {expected_dim}, got {v.shape[0]}"
        )


def validate_complex(v: np.ndarray, name: str = "vector") -> None:
    """
    Validate that a vector is complex-valued (complex128 or complex64).

    Args:
        v:    Vector to validate
        name: Human-readable name for error messages

    Raises:
        TypeError: If v is not complex-valued
    """
    if not np.iscomplexobj(v):
        raise TypeError(f"{name} must be complex-valued, got dtype {v.dtype}")


def validate_real(v: np.ndarray, name: str = "vector") -> None:
    """
    Validate that a vector is real-valued (float64 or float32).

    Args:
        v:    Vector to validate
        name: Human-readable name for error messages

    Raises:
        TypeError: If v is not real-valued
    """
    if np.iscomplexobj(v):
        raise TypeError(f"{name} must be real-valued, got dtype {v.dtype}")
