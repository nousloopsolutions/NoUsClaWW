"""VDS 50000 — The Elevator contract.

The Elevator is the only sanctioned crossing between the Floor (Brain / proprietary
subconscious) and the Surface (public Tool / user-facing world). All data crossing
this boundary must pass through the Elevator. No direct Floor↔Surface traffic is
permitted — this is the Sovereign Sockets boundary enforcement.

The Elevator also implements ruthless session annihilation: when a session is
compromised (revoked token, token-state fluctuation, red-team lethal-trifecta
trigger), the Elevator wipes ALL session state across every subsystem. This is a
non-negotiable, total destruction — no partial state survives.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any


class ElevatorInterface(ABC):
    """VDS 50000 — The Elevator's immutable contract.

    The proprietary implementation lives in /proprietary_core/ (gitignored).
    The public Tool may only cross the Floor↔Surface boundary through this interface.
    """

    @abstractmethod
    def cross_floor_to_surface(self, data: dict[str, Any], session_id: str) -> dict[str, Any]:
        """Send data from the Brain (Floor) to the Tool (Surface).

        This is the only sanctioned upward crossing. The Elevator may redact,
        gate, or refuse data that violates the epistemic boundary or red-team verdict.

        Args:
            data: The payload from the Brain (e.g. BrainResponse fields).
            session_id: The session this crossing belongs to.

        Returns:
            The sanitized payload safe for the Surface. May be empty if gated.
        """
        ...

    @abstractmethod
    def cross_surface_to_floor(self, data: dict[str, Any], session_id: str) -> dict[str, Any]:
        """Send data from the Tool (Surface) to the Brain (Floor).

        This is the only sanctioned downward crossing. The Elevator may strip
        Surface-only metadata before it reaches the Brain.

        Args:
            data: The payload from the Tool (e.g. user input, recall context).
            session_id: The session this crossing belongs to.

        Returns:
            The payload delivered to the Brain.
        """
        ...

    @abstractmethod
    def annihilate_session(self, session_id: str) -> None:
        """VDS 50000 ruthless wipe — destroy ALL session state.

        This is total and irreversible. It deletes every trace of the session
        across every subsystem: KV keys, in-memory caches, tokens, MCP context,
        bridge state. No partial state survives. Used when a session is compromised.

        Args:
            session_id: The session to annihilate.
        """
        ...

    @abstractmethod
    def check_session_state(self, session_id: str) -> str:
        """Return the current lifecycle state of a session.

        Args:
            session_id: The session to inspect.

        Returns:
            One of: "active", "annihilated", or "unknown".
        """
        ...

    @abstractmethod
    def detect_token_fluctuation(
        self, session_id: str, token_state: dict[str, Any]
    ) -> bool:
        """Detect OAuth token state changes (fluctuation) for a session.

        Token-state fluctuation is a signal of session compromise or token theft.
        When detected, the Elevator triggers ruthless annihilation.

        Args:
            session_id: The session whose token state is being checked.
            token_state: The current observed token state (claims, scopes, issuer).

        Returns:
            True if a fluctuation (suspicious change) is detected, False otherwise.
        """
        ...
