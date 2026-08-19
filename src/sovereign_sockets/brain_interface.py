"""VDS 00000 — Abstract protocol for the Brain.

This is the immutable contract that the proprietary Brain (in /proprietary_core/, gitignored)
must implement. The public Tool (in /src/community/ and /src/nousclaww/) can only interact
with the Brain through this interface. No direct imports of proprietary_core are allowed.

The Brain provides:
- Reasoning (thought generation from context)
- Confidence computation (how sure is the Brain about its output)
- Void socket creation (logging what it doesn't know)
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any
from dataclasses import dataclass


@dataclass
class BrainResponse:
    """Response from the Brain's reasoning process."""

    output: str
    confidence: float  # 0.0 to 1.0
    void_sockets: list[str]  # IDs of void sockets created (epistemic gaps)
    metadata: dict[str, Any]


class BrainInterface(ABC):
    """VDS 00000 — The Brain's immutable contract.

    The proprietary implementation lives in /proprietary_core/ (gitignored).
    This interface is the ONLY way the Tool can interact with the Brain.
    """

    @abstractmethod
    def reason(self, context: str, query: str, constraints: dict[str, Any]) -> BrainResponse:
        """Generate a response from the Brain given context and query.

        Args:
            context: The injected memory context (from auto-recall)
            query: The user's input or task
            constraints: Model capability constraints (from model_profiler)

        Returns:
            BrainResponse with output, confidence, void sockets, and metadata

        The Brain MUST:
        - Halt (return empty output + void sockets) when it detects epistemic gaps
        - Never fabricate information it doesn't have
        - Respect the Silence Principle (cost-function determines if silence or caveat)
        """
        ...

    @abstractmethod
    def get_confidence(self, response: str, context: str) -> float:
        """Compute confidence score for a response given its context.

        Returns: 0.0 to 1.0 confidence score.
        Below the dynamic threshold, the epistemic boundary gates the output.
        """
        ...

    @abstractmethod
    def create_void_socket(self, query: str, gap_description: str) -> str:
        """Log an epistemic gap to VDS 90000 (Pool of Tears).

        Args:
            query: The query that couldn't be answered
            gap_description: What information is missing

        Returns: void_socket_id (UUID for tracking)
        """
        ...
