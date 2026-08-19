"""Danger Cones — three-layer hallucination detection and redirect.

Instead of only recording what TO do (invariants, decisions), danger cones
encode what NOT to do — known bad patterns, common hallucinations, and
failure modes. An AI agent can then check its output against these cones and
receive a redirect pointer to the correct approach.

THREE LAYERS OF HALLUCINATION DEFENSE:

  Layer 1 — Text-level hazard markers:
      Simple pattern matching against known bad strings/regexes.
      Cheap, fast, catches obvious hallucinations.

  Layer 2 — Vector danger cones (semantic similarity):
      Encode known bad outputs as vectors. Check cosine similarity
      between generated output and danger cone centroids.
      Catches semantic hallucinations that share meaning but differ in wording.

  Layer 3 — Structural invariants (formal constraints):
      Mathematical constraints that must hold. Violation = hallucination.
      Example: sim in [-1, 1], test_count >= 0, deps form a DAG.

A danger cone is defined as:
    Cone = { v in vector_space : cosine_similarity(v, centroid) > tau }

Where:
    centroid = bundle([bad_pattern_1, bad_pattern_2, ...])
    tau = danger threshold (default 0.65)

This module uses only the Python standard library and numpy. No framework,
UI, or state dependencies.

SYNTH:
    purpose: Three-layer hallucination defense — text regex, vector semantic similarity, and structural invariants — with redirect pointers for known bad patterns.
    axioms: [epistemic_boundary, honest_failure_over_fake_success, evidence_over_intuition, scientific_method]
    objective: Any output checked against the registry is flagged if it matches a known bad pattern at any of the three layers, and the caller receives a redirect to the correct approach instead of a bare rejection.
    anti_patterns:
        - Accepting UNKNOWN as a valid retrieval result
        - Using a static threshold instead of configurable tau per cone
        - Silently swallowing regex compilation errors
        - Importing framework or orchestrator modules (this is a leaf module)
        - Mutating cone centroids after registration
        - Skipping layers — all three must be checked for a full report
"""
#C Adapted from NoUs-fordge Nous-hub mvp_local_core core/danger_cones.py

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

import numpy as np

__all__ = [
    "DangerLevel",
    "DangerCone",
    "DangerConeResult",
    "DangerConeRegistry",
    "HazardMarker",
    "HazardCheckResult",
    "StructuralInvariant",
    "InvariantCheckResult",
    "InvariantChecker",
    "FullHazardReport",
    "check_text_hazards",
    "full_hazard_check",
    "create_default_danger_registry",
    "create_default_hazard_markers",
    "create_default_invariant_checker",
]


# ════════════════════════════════════════════════════════════════════════
# LAYER 1: Text-Level Hazard Markers
# ════════════════════════════════════════════════════════════════════════


@dataclass
class HazardMarker:
    """A text-level hazard marker — a known bad string or regex.

    Attributes:
        pattern: The bad pattern to match (string or regex).
        redirect: What to do instead — the correct approach.
        is_regex: If True, *pattern* is treated as a regex; if False,
            an exact case-insensitive substring match is used.
        severity: How dangerous this pattern is ("high", "medium", "low").
    """

    pattern: str
    redirect: str
    is_regex: bool = False
    severity: str = "high"

    def matches(self, text: str) -> bool:
        """Check if *text* contains this hazard pattern.

        For regex markers, ``re.search`` is used with ``re.IGNORECASE``.
        For non-regex markers, a case-insensitive substring check is used.

        Args:
            text: The text to check.

        Returns:
            True if the pattern matches, False otherwise.
        """
        if not text:
            return False
        if self.is_regex:
            try:
                return bool(re.search(self.pattern, text, re.IGNORECASE))
            except re.error:
                # Fall back to literal match if the regex is invalid.
                return self.pattern.lower() in text.lower()
        return self.pattern.lower() in text.lower()


@dataclass
class HazardCheckResult:
    """Result of checking text against hazard markers (Layer 1).

    Attributes:
        matched: True if any marker matched.
        markers_matched: List of markers that triggered.
        redirects: Redirect messages from triggered markers.
        text: The original text that was checked.
    """

    matched: bool
    markers_matched: list[HazardMarker] = field(default_factory=list)
    redirects: list[str] = field(default_factory=list)
    text: str = ""

    @property
    def severity(self) -> str:
        """Highest severity among matched markers.

        Returns "none" if no markers matched.
        """
        if not self.markers_matched:
            return "none"
        priority = {"high": 3, "medium": 2, "low": 1, "none": 0}
        return max(
            self.markers_matched, key=lambda m: priority.get(m.severity, 0)
        ).severity

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict."""
        return {
            "matched": self.matched,
            "markers_matched": [
                {"pattern": m.pattern, "redirect": m.redirect, "severity": m.severity}
                for m in self.markers_matched
            ],
            "redirects": self.redirects,
            "severity": self.severity,
        }


def check_text_hazards(
    text: str, markers: list[HazardMarker]
) -> HazardCheckResult:
    """Check *text* against all hazard markers (Layer 1).

    Args:
        text: The output text to check.
        markers: List of hazard markers to test against.

    Returns:
        A HazardCheckResult with matched markers and redirects.
    """
    matched: list[HazardMarker] = []
    redirects: list[str] = []
    for marker in markers:
        if marker.matches(text):
            matched.append(marker)
            if marker.redirect:
                redirects.append(marker.redirect)
    return HazardCheckResult(
        matched=len(matched) > 0,
        markers_matched=matched,
        redirects=redirects,
        text=text,
    )


# ════════════════════════════════════════════════════════════════════════
# LAYER 2: Vector Danger Cones (semantic similarity)
# ════════════════════════════════════════════════════════════════════════


class DangerLevel(Enum):
    """Classification of danger cone proximity.

    - SAFE:     Outside all cones — output is likely valid.
    - WARNING:  Near a cone boundary — caution.
    - CRITICAL: Inside a cone — likely hallucination.
    """

    SAFE = "safe"
    WARNING = "warning"
    CRITICAL = "critical"


def _text_to_vector(text: str, dim: int) -> np.ndarray:
    """Encode text as a unit vector using feature hashing (the hashing trick).

    Tokenizes *text* into lowercase alphanumeric words, then hashes each
    token to a dimension index and a sign (+1 or -1). The contributions
    are accumulated into a ``dim``-dimensional vector and L2-normalized.

    This approach ensures that text with overlapping vocabulary produces
    vectors with higher cosine similarity — which is the semantic property
    that danger cones rely on. Unlike random label-based vectors, feature
    hashing captures token-level overlap.

    Args:
        text: The text to encode.
        dim: The dimensionality of the vector space.

    Returns:
        A 1-D numpy array of shape (dim,) with unit L2 norm. Returns a
        zero vector if *text* has no tokens.
    """
    vec = np.zeros(dim, dtype=np.float64)
    tokens = re.findall(r"\w+", text.lower())
    for token in tokens:
        h = hashlib.sha256(token.encode("utf-8")).digest()
        # Use first 4 bytes for index, next byte for sign.
        idx = int.from_bytes(h[:4], "little") % dim
        sign = 1.0 if (h[4] & 1) == 0 else -1.0
        vec[idx] += sign
    norm = np.linalg.norm(vec)
    if norm > 1e-12:
        return vec / norm
    return vec


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two vectors.

    Args:
        a: First vector.
        b: Second vector (must have the same length as *a*).

    Returns:
        Cosine similarity in [-1.0, 1.0], clamped to handle float
        precision drift.
    """
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na < 1e-12 or nb < 1e-12:
        return 0.0
    sim = float(np.dot(a, b) / (na * nb))
    # Clamp to [-1, 1] to guard against float32 precision drift.
    return max(-1.0, min(1.0, sim))


@dataclass
class DangerCone:
    """A vector danger cone — a region in vector space encoding known bad patterns.

    Attributes:
        name: Human-readable identifier for this cone.
        centroid: Unit vector — the bundle of known bad pattern vectors.
        tau_warning: Similarity threshold for WARNING classification.
        tau_critical: Similarity threshold for CRITICAL classification.
        redirect_text: What the agent should do instead.
        bad_patterns: List of text patterns encoded into the centroid.
    """

    name: str
    centroid: np.ndarray
    tau_warning: float = 0.45
    tau_critical: float = 0.65
    redirect_text: str = ""
    bad_patterns: list[str] = field(default_factory=list)

    def check(self, vector: np.ndarray) -> tuple[DangerLevel, float]:
        """Check a vector against this danger cone.

        Args:
            vector: The output vector to check (must have the same
                dimensionality as *centroid*).

        Returns:
            A tuple of (DangerLevel, similarity_score).
        """
        sim = _cosine_similarity(vector, self.centroid)
        if sim >= self.tau_critical:
            return DangerLevel.CRITICAL, sim
        if sim >= self.tau_warning:
            return DangerLevel.WARNING, sim
        return DangerLevel.SAFE, sim


@dataclass
class DangerConeResult:
    """Result of checking a vector against all danger cones (Layer 2).

    Attributes:
        level: The worst (highest danger) level across all cones.
        cones_triggered: List of (cone, level, similarity) tuples for
            cones that were triggered (not SAFE).
        redirects: Redirect messages from triggered cones.
        max_similarity: The highest similarity score observed.
    """

    level: DangerLevel = DangerLevel.SAFE
    cones_triggered: list[tuple[DangerCone, DangerLevel, float]] = field(
        default_factory=list
    )
    redirects: list[str] = field(default_factory=list)
    max_similarity: float = 0.0

    @property
    def is_safe(self) -> bool:
        """True if no cones were triggered."""
        return self.level == DangerLevel.SAFE

    @property
    def is_dangerous(self) -> bool:
        """True if the level is CRITICAL."""
        return self.level == DangerLevel.CRITICAL

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict."""
        return {
            "level": self.level.value,
            "is_safe": self.is_safe,
            "cones_triggered": [
                {"name": c.name, "level": lv.value, "similarity": round(s, 4)}
                for c, lv, s in self.cones_triggered
            ],
            "redirects": self.redirects,
            "max_similarity": round(self.max_similarity, 4),
        }


class DangerConeRegistry:
    """Registry of danger cones for hallucination detection.

    Cones are created from known bad patterns. Each bad pattern is encoded
    as a deterministic vector and bundled (averaged) into the cone's
    centroid. When an output vector is checked, cosine similarity to each
    centroid determines whether the output falls within a danger cone.

    Usage::

        registry = DangerConeRegistry(dim=1024)
        registry.add_cone(
            name="fresh_query_retrieval",
            bad_patterns=[
                "generate fresh query vector for retrieval",
                "create new vector instead of fetching engram",
            ],
            redirect_text="fetch stored engram from canon — do not generate fresh vectors",
        )
        result = registry.check_text("generate fresh query vector for retrieval")
        if result.is_dangerous:
            print(f"HALUCINATION DETECTED: {result.redirects}")
    """

    def __init__(self, dim: int = 1024) -> None:
        """Initialize the registry.

        Args:
            dim: The dimensionality of the vector space. All cones and
                checked vectors must use this dimension.
        """
        self.dim = dim
        self.cones: list[DangerCone] = []

    def add_cone(
        self,
        name: str,
        bad_patterns: list[str],
        redirect_text: str = "",
        tau_warning: float = 0.45,
        tau_critical: float = 0.65,
    ) -> DangerCone:
        """Create and register a new danger cone from bad patterns.

        Each bad pattern is encoded as a feature-hashed vector (token-level
        semantic encoding). The centroid is the L2-normalized average of
        all bad pattern vectors.

        Args:
            name: Human-readable identifier for this cone.
            bad_patterns: List of known bad text patterns.
            redirect_text: What the agent should do instead.
            tau_warning: Similarity threshold for WARNING level.
            tau_critical: Similarity threshold for CRITICAL level.

        Returns:
            The created DangerCone.

        Raises:
            ValueError: If *bad_patterns* is empty.
        """
        if not bad_patterns:
            raise ValueError("At least one bad pattern required")

        vectors: list[np.ndarray] = []
        for pattern in bad_patterns:
            v = _text_to_vector(pattern, dim=self.dim)
            vectors.append(v)

        centroid = np.mean(vectors, axis=0)
        norm = np.linalg.norm(centroid)
        if norm > 1e-12:
            centroid = centroid / norm

        cone = DangerCone(
            name=name,
            centroid=centroid,
            tau_warning=tau_warning,
            tau_critical=tau_critical,
            redirect_text=redirect_text,
            bad_patterns=list(bad_patterns),
        )
        self.cones.append(cone)
        return cone

    def check(self, vector: np.ndarray) -> DangerConeResult:
        """Check a vector against all registered danger cones.

        Returns the worst (highest danger) result across all cones.

        Args:
            vector: The output vector to check.

        Returns:
            A DangerConeResult with the worst level and all triggered cones.
        """
        triggered: list[tuple[DangerCone, DangerLevel, float]] = []
        redirects: list[str] = []
        max_sim = 0.0
        worst_level = DangerLevel.SAFE

        level_priority = {
            DangerLevel.SAFE: 0,
            DangerLevel.WARNING: 1,
            DangerLevel.CRITICAL: 2,
        }

        for cone in self.cones:
            level, sim = cone.check(vector)
            if sim > max_sim:
                max_sim = sim
            if level != DangerLevel.SAFE:
                triggered.append((cone, level, sim))
                if cone.redirect_text:
                    redirects.append(f"[{cone.name}] {cone.redirect_text}")
            if level_priority[level] > level_priority[worst_level]:
                worst_level = level

        return DangerConeResult(
            level=worst_level,
            cones_triggered=triggered,
            redirects=redirects,
            max_similarity=max_sim,
        )

    def check_text(self, text: str) -> DangerConeResult:
        """Encode *text* as a vector and check against all cones.

        This is the primary interface for AI agents: encode your output
        text as a vector, then check it against the danger cones.

        Args:
            text: The output text to check.

        Returns:
            A DangerConeResult.
        """
        vector = _text_to_vector(text, dim=self.dim)
        return self.check(vector)

    def list_cones(self) -> list[str]:
        """Return the names of all registered cones.

        Returns:
            A list of cone names in registration order.
        """
        return [c.name for c in self.cones]


# ════════════════════════════════════════════════════════════════════════
# LAYER 3: Structural Invariants (formal constraints)
# ════════════════════════════════════════════════════════════════════════


@dataclass
class StructuralInvariant:
    """A formal mathematical constraint that must hold.

    Attributes:
        name: Identifier for this invariant.
        check: A callable that takes a value and returns True if valid.
        message: Error message if the invariant is violated.
        redirect: What to do to fix the violation.
        applies_to: Optional type predicate — if set, the invariant only
            runs when ``applies_to(value)`` returns True. This prevents
            e.g. a vector-dimension check from firing on a scalar.
    """

    name: str
    check: Callable[[Any], bool]
    message: str = ""
    redirect: str = ""
    applies_to: Optional[Callable[[Any], bool]] = None


@dataclass
class InvariantCheckResult:
    """Result of checking values against structural invariants (Layer 3).

    Attributes:
        all_passed: True if all applicable invariants passed.
        violations: List of (invariant, value) tuples for violations.
        redirects: Redirect messages from violated invariants.
    """

    all_passed: bool
    violations: list[tuple[StructuralInvariant, Any]] = field(
        default_factory=list
    )
    redirects: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict."""
        return {
            "all_passed": self.all_passed,
            "violations": [
                {"name": inv.name, "message": inv.message}
                for inv, _ in self.violations
            ],
            "redirects": self.redirects,
        }


class InvariantChecker:
    """Check structural invariants — formal constraints that must hold.

    These are mathematical properties, not patterns. Violation means the
    output is structurally invalid (a hallucination of form).

    Example invariants:
        - cosine_similarity in [-1, 1]
        - test_count >= 0
        - deps form a DAG (no cycles)
        - vector dimension matches expected
    """

    def __init__(self) -> None:
        self.invariants: list[StructuralInvariant] = []

    def add(self, invariant: StructuralInvariant) -> None:
        """Register a structural invariant.

        Args:
            invariant: The invariant to add.
        """
        self.invariants.append(invariant)

    def check(self, value: Any) -> InvariantCheckResult:
        """Check a value against all registered invariants.

        Invariants whose ``applies_to`` predicate returns False are skipped.

        Args:
            value: The value to check.

        Returns:
            An InvariantCheckResult with any violations.
        """
        violations: list[tuple[StructuralInvariant, Any]] = []
        redirects: list[str] = []

        for inv in self.invariants:
            if inv.applies_to is not None:
                try:
                    if not inv.applies_to(value):
                        continue
                except Exception:
                    continue
            try:
                if not inv.check(value):
                    violations.append((inv, value))
                    if inv.redirect:
                        redirects.append(f"[{inv.name}] {inv.redirect}")
            except Exception as exc:
                violations.append((inv, value))
                if inv.redirect:
                    redirects.append(
                        f"[{inv.name}] {inv.redirect} (check raised: {exc})"
                    )

        return InvariantCheckResult(
            all_passed=len(violations) == 0,
            violations=violations,
            redirects=redirects,
        )


# ════════════════════════════════════════════════════════════════════════
# COMBINED CHECK: All three layers
# ════════════════════════════════════════════════════════════════════════


@dataclass
class FullHazardReport:
    """Combined result from all three hazard detection layers.

    Attributes:
        text_hazards: Layer 1 result.
        cone_check: Layer 2 result.
        invariant_check: Layer 3 result (or None if not run).
    """

    text_hazards: HazardCheckResult
    cone_check: DangerConeResult
    invariant_check: Optional[InvariantCheckResult] = None

    @property
    def is_safe(self) -> bool:
        """True only if all layers pass."""
        if self.text_hazards.matched:
            return False
        if not self.cone_check.is_safe:
            return False
        if self.invariant_check is not None and not self.invariant_check.all_passed:
            return False
        return True

    @property
    def danger_level(self) -> DangerLevel:
        """Overall danger level — worst across all layers."""
        if self.text_hazards.matched:
            return DangerLevel.CRITICAL
        if self.invariant_check and not self.invariant_check.all_passed:
            return DangerLevel.CRITICAL
        return self.cone_check.level

    @property
    def all_redirects(self) -> list[str]:
        """All redirect messages from all layers."""
        redirects: list[str] = []
        redirects.extend(self.text_hazards.redirects)
        redirects.extend(self.cone_check.redirects)
        if self.invariant_check:
            redirects.extend(self.invariant_check.redirects)
        return redirects

    def summary(self) -> str:
        """Human-readable summary of the hazard report."""
        lines: list[str] = []
        level = self.danger_level
        lines.append(f"HAZARD REPORT: {level.value.upper()}")
        lines.append(
            f"  Text hazards: {'MATCHED' if self.text_hazards.matched else 'clear'}"
        )
        lines.append(
            f"  Cone check: {self.cone_check.level.value} "
            f"(max_sim={self.cone_check.max_similarity:.3f})"
        )
        if self.invariant_check:
            lines.append(
                f"  Invariants: "
                f"{'PASSED' if self.invariant_check.all_passed else 'VIOLATED'}"
            )
        if self.all_redirects:
            lines.append("  Redirects:")
            for r in self.all_redirects:
                lines.append(f"    -> {r}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict."""
        return {
            "is_safe": self.is_safe,
            "danger_level": self.danger_level.value,
            "text_hazards": self.text_hazards.to_dict(),
            "cone_check": self.cone_check.to_dict(),
            "invariant_check": (
                self.invariant_check.to_dict() if self.invariant_check else None
            ),
            "all_redirects": self.all_redirects,
        }


def full_hazard_check(
    text: str,
    text_markers: list[HazardMarker],
    cone_registry: DangerConeRegistry,
    invariant_checker: Optional[InvariantChecker] = None,
    invariant_value: Any = None,
) -> FullHazardReport:
    """Run all three layers of hazard detection.

    Args:
        text: The output text to check.
        text_markers: Layer 1 — text-level hazard markers.
        cone_registry: Layer 2 — vector danger cones.
        invariant_checker: Layer 3 — structural invariants (optional).
        invariant_value: Value to check against invariants (optional).

    Returns:
        A FullHazardReport with results from all layers.
    """
    # Layer 1: Text-level
    text_result = check_text_hazards(text, text_markers)

    # Layer 2: Vector cones
    cone_result = cone_registry.check_text(text)

    # Layer 3: Structural invariants
    inv_result: Optional[InvariantCheckResult] = None
    if invariant_checker is not None and invariant_value is not None:
        inv_result = invariant_checker.check(invariant_value)

    return FullHazardReport(
        text_hazards=text_result,
        cone_check=cone_result,
        invariant_check=inv_result,
    )


# ════════════════════════════════════════════════════════════════════════
# BUILT-IN CONES: Known hallucination patterns
# ════════════════════════════════════════════════════════════════════════


def create_default_danger_registry(dim: int = 1024) -> DangerConeRegistry:
    """Create a danger cone registry pre-loaded with common hallucination patterns.

    These are general-purpose bad patterns that apply to most AI agent
    systems — fresh-vector generation, accepting UNKNOWN results,
    unbounded growth, and log-based monitoring.

    Returns:
        A DangerConeRegistry with pre-registered cones.
    """
    registry = DangerConeRegistry(dim=dim)

    registry.add_cone(
        name="fresh_query_retrieval",
        bad_patterns=[
            "generate fresh query vector for retrieval",
            "create new vector instead of fetching engram",
            "compute query_vec from scratch for lookup",
            "generate query instead of canon.get",
        ],
        redirect_text=(
            "fetch stored engram from canon via canon.get(query_label) — "
            "generating fresh vectors returns UNKNOWN"
        ),
    )

    registry.add_cone(
        name="accept_unknown_valid",
        bad_patterns=[
            "accept UNKNOWN as valid retrieval result",
            "treat UNKNOWN as successful retrieval",
            "pass UNKNOWN result without checking epistemic gate",
        ],
        redirect_text=(
            "UNKNOWN means retrieval failed — never accept it as valid. "
            "Check epistemic_gate.classify() first."
        ),
    )

    registry.add_cone(
        name="unbounded_growth",
        bad_patterns=[
            "use unbounded list for tracking",
            "append to list without eviction",
            "grow data structure without limit",
        ],
        redirect_text=(
            "use bounded structures (circular buffer, fixed histogram, LRU cache)"
        ),
    )

    registry.add_cone(
        name="log_monitoring",
        bad_patterns=[
            "use log-based monitoring",
            "write monitoring data as log prose",
            "track metrics via print statements",
        ],
        redirect_text=(
            "monitoring data must be structured (JSON-serializable) and "
            "queryable — not log prose"
        ),
    )

    return registry


def create_default_hazard_markers() -> list[HazardMarker]:
    """Create text-level hazard markers from common failure patterns.

    These are the Layer 1 cheap checks — exact string or regex matches
    against known bad outputs.

    Returns:
        A list of HazardMarker objects.
    """
    return [
        HazardMarker(
            pattern="UNKNOWN sim=0.057",
            redirect=(
                "This is the signature of fresh-vector retrieval failure — "
                "fetch from canon.get() instead"
            ),
            severity="high",
        ),
        HazardMarker(
            pattern="sim=1.0000000000000002",
            redirect=(
                "float32 precision drift — clamp similarity to [-1,1]"
            ),
            severity="medium",
        ),
        HazardMarker(
            pattern=r"P>=0\.8.*KNOWN",
            redirect=(
                "NO probability bins — sigmoid confidence is RANKING score "
                "only, hard threshold is SOLE classifier"
            ),
            severity="high",
            is_regex=True,
        ),
        HazardMarker(
            pattern="no failures yet",
            redirect=(
                "record every failed approach — 'no failures yet' loses "
                "highest-value learning"
            ),
            severity="low",
        ),
    ]


def create_default_invariant_checker() -> InvariantChecker:
    """Create structural invariant checks for common outputs.

    Returns:
        An InvariantChecker with pre-registered invariants for similarity
        ranges, non-negative counts, and vector dimensions.
    """
    checker = InvariantChecker()

    checker.add(
        StructuralInvariant(
            name="sim_range",
            check=lambda v: isinstance(v, (int, float))
            and -1.0 <= v <= 1.0,
            message="cosine similarity must be in [-1, 1]",
            redirect=(
                "clamp similarity to [-1,1] — float32 precision can "
                "produce 1.0000000000000002"
            ),
            applies_to=lambda v: isinstance(v, (int, float))
            and not isinstance(v, bool),
        )
    )

    checker.add(
        StructuralInvariant(
            name="test_count_nonneg",
            check=lambda v: isinstance(v, (int, float)) and v >= 0,
            message="test counts must be non-negative",
            redirect=(
                "verify test runner output — negative counts indicate a "
                "parsing error"
            ),
            applies_to=lambda v: isinstance(v, (int, float))
            and not isinstance(v, bool),
        )
    )

    checker.add(
        StructuralInvariant(
            name="vector_dim",
            check=lambda v: isinstance(v, np.ndarray)
            and v.ndim >= 1
            and v.shape[-1] in (32, 128, 256, 512, 1024, 10000),
            message=(
                "vector dimension must be a standard size "
                "(32, 128, 256, 512, 1024, or 10000)"
            ),
            redirect="use a standard dimension constant for vector generation",
            applies_to=lambda v: isinstance(v, np.ndarray),
        )
    )

    return checker
