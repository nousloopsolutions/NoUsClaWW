"""VDS 90000 — Pool of Tears socket storage contract.

The Void Archive stores epistemic gaps — the things the Brain does NOT know. Every
time the Brain halts (Silence Principle), it creates a void socket recording the
query and the missing information. These sockets are the institutional memory of
ignorance: they are never silently deleted, only resolved when the gap is filled.

The Pool of Tears is the substrate for honest learning. Resolving a void socket
means the Brain has acquired the missing knowledge (via study, tool use, or human
input) and can now answer the original query. Unresolved sockets are a permanent
record of the boundary of competence.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Optional
from dataclasses import dataclass


@dataclass
class VoidSocket:
    """A single epistemic gap record in the Pool of Tears (VDS 90000).

    Attributes:
        id: UUID for tracking the void socket.
        query: The query that couldn't be answered.
        gap_description: What information was missing.
        session_id: The session in which the gap was detected.
        created_at: ISO-8601 timestamp of creation.
        resolved: Whether the gap has been filled.
        resolution: The resolution text if resolved, else None.
    """

    id: str
    query: str
    gap_description: str
    session_id: str
    created_at: str
    resolved: bool
    resolution: Optional[str]


class VoidArchiveInterface(ABC):
    """VDS 90000 — The Pool of Tears storage contract.

    The proprietary implementation lives in /proprietary_core/ (gitignored).
    The public Tool may only record and query void sockets through this interface.
    """

    @abstractmethod
    def store_void_socket(
        self, query: str, gap_description: str, session_id: str
    ) -> str:
        """Record a new epistemic gap in the Pool of Tears.

        Args:
            query: The query that couldn't be answered.
            gap_description: What information was missing.
            session_id: The session in which the gap was detected.

        Returns:
            The void_socket_id (UUID) for tracking.
        """
        ...

    @abstractmethod
    def retrieve_void_socket(self, void_socket_id: str) -> Optional[VoidSocket]:
        """Retrieve a single void socket by ID.

        Args:
            void_socket_id: The UUID of the void socket.

        Returns:
            The VoidSocket if found, else None.
        """
        ...

    @abstractmethod
    def list_unresolved_void_sockets(self) -> list[VoidSocket]:
        """List all unresolved void sockets (open epistemic gaps).

        Returns:
            A list of unresolved VoidSocket records.
        """
        ...

    @abstractmethod
    def resolve_void_socket(self, void_socket_id: str, resolution: str) -> None:
        """Mark a void socket as resolved with the given resolution text.

        Resolving means the missing knowledge has been acquired and the gap closed.
        This never deletes the socket — it records the resolution for auditability.

        Args:
            void_socket_id: The UUID of the void socket to resolve.
            resolution: The text describing how the gap was filled.
        """
        ...

    @abstractmethod
    def get_void_socket_stats(self) -> dict:
        """Return aggregate statistics over the Pool of Tears.

        Returns:
            A dict with keys: "total", "resolved", "unresolved", "by_category".
            "by_category" maps gap-category strings to counts.
        """
        ...
