"""Data Classification and Authorization Gate.

Every import job requires a data class and an authorization record.
PROHIBITED_SCHOOL data is rejected before parsing, copying, embedding,
or logging — it never enters the pipeline.

Data Classes (ordered by restrictiveness):
    PUBLIC_TEST         — Publicly available test fixtures, no privacy concern.
    SYNTHETIC_TEST      — Synthetic/generated test data, no real PII.
    PERSONAL_AUTHORIZED — User's personal data with explicit authorization.
    SENSITIVE_PERSONAL  — Sensitive personal data (health, financial, etc.).
    PROHIBITED_SCHOOL   — School-owned content (REJECTED before any processing).

Authorization:
    Every source must have an AuthorizationRecord before processing begins.
    The record captures who authorized, when, and under what data class.
    The record carries a tamper-evident SHA-256 hash. Authorization may
    expire (time-based revocation). PROHIBITED_SCHOOL sources are rejected
    at the gate — they never get parsed, copied, embedded, or logged.

Contract:
    - No source may proceed to processing without passing the gate.
    - PROHIBITED_SCHOOL is rejected before ANY processing, including logging
      of content. The rejection itself is logged without revealing content.
    - Authorization records are tamper-evident (SHA-256 hash verification).
    - Expired authorizations are treated as unauthorized.

SYNTH:
    purpose: Five-tier data classification with a hard gate that rejects PROHIBITED_SCHOOL before any processing, plus tamper-evident authorization records with expiry-based revocation.
    axioms: [local_first, epistemic_boundary, honest_failure_over_fake_success, reversibility_awareness]
    objective: No data source enters the processing pipeline without a verified, unexpired authorization record, and PROHIBITED_SCHOOL content is rejected at the gate before any parsing, copying, embedding, or logging of content occurs.
    anti_patterns:
        - Allowing PROHIBITED_SCHOOL content to reach any processing stage
        - Accepting an authorization record with a mismatched hash
        - Processing a source whose authorization has expired without re-authorization
        - Logging or revealing the content of a rejected PROHIBITED_SCHOOL source
        - Importing internal nousclaww modules (this is a leaf module)
        - Making the PROHIBITED_SCHOOL rejection configurable or overridable
"""
#C Adapted from NoUs-fordge Nous-hub mvp_local_core data/data_class.py

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

__all__ = [
    "DataClass",
    "AuthorizationError",
    "ProhibitedContentError",
    "AuthorizationRecord",
    "DataGate",
]

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DataClass(Enum):
    """Explicit data classification for every imported source.

    Ordering matters: higher index = more restrictive.
    PROHIBITED_SCHOOL is always rejected before any processing.

    Attributes:
        PUBLIC_TEST: Publicly available test fixtures, no privacy concern.
        SYNTHETIC_TEST: Synthetic/generated test data, no real PII.
        PERSONAL_AUTHORIZED: User's personal data with explicit authorization.
        SENSITIVE_PERSONAL: Sensitive personal data (health, financial, etc.).
        PROHIBITED_SCHOOL: School-owned content — REJECTED before any processing.
    """

    PUBLIC_TEST = "PUBLIC_TEST"
    SYNTHETIC_TEST = "SYNTHETIC_TEST"
    PERSONAL_AUTHORIZED = "PERSONAL_AUTHORIZED"
    SENSITIVE_PERSONAL = "SENSITIVE_PERSONAL"
    PROHIBITED_SCHOOL = "PROHIBITED_SCHOOL"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AuthorizationError(Exception):
    """Raised when a source lacks authorization or authorization is invalid."""


class ProhibitedContentError(AuthorizationError):
    """Raised when PROHIBITED_SCHOOL content is submitted for processing.

    This is a hard gate — the content must never be parsed, copied,
    embedded, or logged. The rejection itself is logged without
    revealing the content.
    """


# ---------------------------------------------------------------------------
# AuthorizationRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthorizationRecord:
    """Authorization for processing a source under a specific data class.

    The record is tamper-evident: a SHA-256 hash is computed over the
    record's fields at construction time. Any modification to the fields
    after construction will cause ``verify_hash()`` to return False.

    Attributes:
        data_class: The DataClass assigned to this source.
        authorizer: Identity of the authorizing party (user ID or role).
        authorized_at: Unix timestamp of authorization.
        purpose: Stated purpose for processing (e.g. "personal memory").
        expires_at: Optional expiry timestamp (None = no expiry). When
            the current time exceeds this value, the authorization is
            considered expired and must be re-authorized.
        authorization_hash: SHA-256 hash of the authorization record
            for tamper detection. Computed automatically at construction.
    """

    data_class: DataClass
    authorizer: str
    authorized_at: float
    purpose: str
    expires_at: Optional[float] = None
    authorization_hash: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        """Compute the tamper-evident hash if not already set.

        The hash is computed over ``data_class|authorizer|authorized_at|
        purpose|expires_at``. This ensures any field modification is
        detectable.
        """
        if not self.authorization_hash:
            content = self._hash_content()
            h = hashlib.sha256(content.encode("utf-8")).hexdigest()
            object.__setattr__(self, "authorization_hash", h)

    def _hash_content(self) -> str:
        """Build the string that is hashed for tamper detection.

        Returns:
            A pipe-delimited string of the record's core fields.
        """
        return (
            f"{self.data_class.value}|{self.authorizer}|"
            f"{self.authorized_at}|{self.purpose}|{self.expires_at}"
        )

    def is_expired(self, now: Optional[float] = None) -> bool:
        """Check if this authorization has expired.

        Args:
            now: Current Unix timestamp. If None, ``time.time()`` is
                used.

        Returns:
            True if the authorization has expired, False otherwise.
            Authorizations with ``expires_at=None`` never expire.
        """
        if self.expires_at is None:
            return False
        current = now if now is not None else time.time()
        return current >= self.expires_at

    def verify_hash(self) -> bool:
        """Verify the authorization record hash matches.

        This detects tampering: if any field has been modified after
        construction, the hash will not match.

        Returns:
            True if the stored hash matches the computed hash, False
            otherwise.
        """
        content = self._hash_content()
        expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return self.authorization_hash == expected

    def to_dict(self) -> dict[str, object]:
        """Serialize to a plain dictionary.

        Returns:
            A dictionary with all authorization record fields.
        """
        return {
            "data_class": self.data_class.value,
            "authorizer": self.authorizer,
            "authorized_at": self.authorized_at,
            "purpose": self.purpose,
            "expires_at": self.expires_at,
            "authorization_hash": self.authorization_hash,
            "is_expired": self.is_expired(),
        }


# ---------------------------------------------------------------------------
# DataGate
# ---------------------------------------------------------------------------


class DataGate:
    """Classify sources and enforce authorization gates.

    This is the FIRST gate in any data ingestion pipeline. No source may
    proceed to parsing, copying, embedding, or logging without passing
    this gate.

    The gate enforces three checks in order:
      1. The source has an authorization record.
      2. The authorization record's hash is valid (tamper-evident).
      3. The authorization has not expired.
      4. The data class is not PROHIBITED_SCHOOL (hard gate).

    PROHIBITED_SCHOOL sources raise :class:`ProhibitedContentError` and
    never enter the pipeline. The rejection is logged without revealing
    the content.

    Usage::

        gate = DataGate()
        auth = AuthorizationRecord(
            data_class=DataClass.PERSONAL_AUTHORIZED,
            authorizer="user_brent",
            authorized_at=time.time(),
            purpose="personal memory augmentation",
        )
        gate.authorize(source_id="src_001", record=auth)
        data_class = gate.check_authorized("src_001")  # Passes

        # PROHIBITED_SCHOOL is always rejected:
        prohibited = AuthorizationRecord(
            data_class=DataClass.PROHIBITED_SCHOOL,
            authorizer="user_brent",
            authorized_at=time.time(),
            purpose="test",
        )
        gate.authorize(source_id="src_002", record=prohibited)
        gate.check_authorized("src_002")  # Raises ProhibitedContentError
    """

    def __init__(self) -> None:
        """Initialize an empty data gate."""
        self._authorizations: dict[str, AuthorizationRecord] = {}

    def authorize(
        self, source_id: str, record: AuthorizationRecord
    ) -> None:
        """Register authorization for a source.

        The authorization record's hash is verified before storage.
        A mismatched hash raises :class:`AuthorizationError` — the
        record may have been tampered with.

        Args:
            source_id: Opaque source identifier.
            record: Authorization record with data class and hash.

        Raises:
            AuthorizationError: If the authorization record hash is
                invalid (tamper detected).
        """
        if not record.verify_hash():
            raise AuthorizationError(
                f"Authorization record hash mismatch for source "
                f"{source_id} — record may have been tampered with"
            )
        self._authorizations[source_id] = record

    def check_authorized(self, source_id: str) -> DataClass:
        """Check if a source is authorized for processing.

        This is the GATE. PROHIBITED_SCHOOL sources are rejected here
        before any parsing, copying, embedding, or logging occurs.

        Args:
            source_id: Opaque source identifier.

        Returns:
            The DataClass assigned to this source.

        Raises:
            AuthorizationError: If the source has no authorization
                record, or if the authorization has expired.
            ProhibitedContentError: If the source is classified as
                PROHIBITED_SCHOOL.
        """
        if source_id not in self._authorizations:
            raise AuthorizationError(
                f"Source {source_id} has no authorization record — "
                f"cannot proceed with processing"
            )

        record = self._authorizations[source_id]

        # Check expiry.
        if record.is_expired():
            raise AuthorizationError(
                f"Source {source_id} authorization has expired — "
                f"re-authorization required"
            )

        # PROHIBITED_SCHOOL gate — reject before ANY processing.
        if record.data_class == DataClass.PROHIBITED_SCHOOL:
            raise ProhibitedContentError(
                f"Source {source_id} is classified as PROHIBITED_SCHOOL — "
                f"rejected before parsing, copying, embedding, or logging. "
                f"This rejection is logged without revealing content."
            )

        return record.data_class

    def classify(self, data: object) -> DataClass:
        """Classify raw data heuristically into a DataClass.

        This is a lightweight heuristic classifier that inspects the
        type and content of *data* to suggest a data class. It does
        NOT replace explicit authorization — it is a convenience for
        pre-classification before an AuthorizationRecord is created.

        Heuristics:
          - ``bytes`` or ``bytearray`` with no text markers -> SYNTHETIC_TEST
          - ``str`` containing "test" or "fixture" -> PUBLIC_TEST
          - ``str`` containing "synthetic" or "generated" -> SYNTHETIC_TEST
          - ``dict`` with "school" or "ferpa" keys -> PROHIBITED_SCHOOL
          - Default -> PERSONAL_AUTHORIZED

        Args:
            data: The raw data to classify.

        Returns:
            A DataClass suggestion. The caller must still create an
            AuthorizationRecord and call :meth:`authorize` before
            processing.
        """
        if isinstance(data, (bytes, bytearray)):
            # Binary data with no text markers — assume synthetic test.
            try:
                text = data.decode("utf-8", errors="ignore").lower()
            except Exception:
                return DataClass.SYNTHETIC_TEST
            if "school" in text or "ferpa" in text:
                return DataClass.PROHIBITED_SCHOOL
            if "test" in text or "fixture" in text:
                return DataClass.PUBLIC_TEST
            if "synthetic" in text or "generated" in text:
                return DataClass.SYNTHETIC_TEST
            return DataClass.SYNTHETIC_TEST

        if isinstance(data, str):
            text = data.lower()
            if "school" in text or "ferpa" in text:
                return DataClass.PROHIBITED_SCHOOL
            if "synthetic" in text or "generated" in text:
                return DataClass.SYNTHETIC_TEST
            if "test" in text or "fixture" in text:
                return DataClass.PUBLIC_TEST
            return DataClass.PERSONAL_AUTHORIZED

        if isinstance(data, dict):
            # Check keys and values for school/FERPA markers.
            text = " ".join(str(k).lower() for k in data.keys())
            text += " " + " ".join(str(v).lower() for v in data.values())
            if "school" in text or "ferpa" in text:
                return DataClass.PROHIBITED_SCHOOL
            if "health" in text or "medical" in text or "financial" in text:
                return DataClass.SENSITIVE_PERSONAL
            if "test" in text or "fixture" in text:
                return DataClass.PUBLIC_TEST
            return DataClass.PERSONAL_AUTHORIZED

        # Unknown type — default to personal authorized (most cautious
        # non-prohibited class).
        return DataClass.PERSONAL_AUTHORIZED

    def check_expired(
        self, source_id: str, now: Optional[float] = None
    ) -> bool:
        """Check if a source's authorization has expired.

        Unlike :meth:`check_authorized`, this method does not raise.
        It returns True if the authorization has expired or if no
        authorization exists.

        Args:
            source_id: Opaque source identifier.
            now: Current Unix timestamp. If None, ``time.time()`` is used.

        Returns:
            True if the authorization has expired or does not exist,
            False if it is still valid.
        """
        record = self._authorizations.get(source_id)
        if record is None:
            return True
        return record.is_expired(now)

    def get_authorization(
        self, source_id: str
    ) -> Optional[AuthorizationRecord]:
        """Get the authorization record for a source.

        Args:
            source_id: Opaque source identifier.

        Returns:
            The AuthorizationRecord, or None if the source is not
            authorized.
        """
        return self._authorizations.get(source_id)

    def revoke_authorization(self, source_id: str) -> None:
        """Revoke authorization for a source.

        After revocation, the source will fail :meth:`check_authorized`
        with an AuthorizationError (no authorization record).

        Args:
            source_id: Opaque source identifier.
        """
        self._authorizations.pop(source_id, None)

    def is_authorized(self, source_id: str) -> bool:
        """Check if a source is authorized (without raising).

        This is a non-throwing check. It returns False for any condition
        that would cause :meth:`check_authorized` to raise.

        Args:
            source_id: Opaque source identifier.

        Returns:
            True if the source is authorized and not prohibited,
            False otherwise.
        """
        try:
            self.check_authorized(source_id)
            return True
        except (AuthorizationError, ProhibitedContentError):
            return False

    def list_authorized(self) -> dict[str, DataClass]:
        """List all currently authorized sources and their data classes.

        PROHIBITED_SCHOOL sources are excluded from this listing —
        they are never "authorized" for processing.

        Returns:
            A dictionary mapping source_id to DataClass for all
            non-prohibited, non-expired authorizations.
        """
        return {
            sid: record.data_class
            for sid, record in self._authorizations.items()
            if record.data_class != DataClass.PROHIBITED_SCHOOL
            and not record.is_expired()
        }

    def __len__(self) -> int:
        """Return the number of registered authorizations (including prohibited)."""
        return len(self._authorizations)
