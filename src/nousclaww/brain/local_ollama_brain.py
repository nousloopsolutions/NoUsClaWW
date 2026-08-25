"""LocalOllamaBrain — community BrainInterface implementation backed by Ollama.

This module implements ``sovereign_sockets.brain_interface.BrainInterface``
using the existing :mod:`nousclaww.llm_interface.LLMInterface`, which talks
to a local Ollama server. It is the default "brain" for the socket-plugin
integration: when the Nous MVP loads NoUsClaWW as a socket plugin, it gets
a concrete brain that reasons on the user's local GPU.

Design notes:
- Confidence is derived from the epistemic-boundary abstention helpers in
  ``llm_interface``. When the LLM abstains with "UNKNOWN: ...", confidence
  is 0.0 and a void socket is created. Otherwise confidence is a simple
  heuristic based on whether context was provided (0.7) or not (0.5).
- Void sockets are logged as UUIDs and stored in an in-memory list. A
  production brain would persist them to VDS 90000; the community impl
  keeps them in-process so tests run without external services.
- The ``ollama`` package is an optional dependency. If it is not installed,
  constructing ``LocalOllamaBrain`` raises ``ImportError`` with a helpful
  message, but the module itself imports cleanly (AGENTS.md Section 4.4).

SYNTH:
    purpose: Community BrainInterface implementation backed by a local Ollama LLM.
    axioms: [local_first, llm_agnostic, epistemic_boundary, honest_failure_over_fake_success]
    objective: Provide a concrete brain that reasons on the user's local GPU,
        computes honest confidence, and logs epistemic gaps as void sockets —
        all without cloud dependencies.
    anti_patterns:
        - Never fabricate output when the LLM fails — return silence + void socket
        - Never report high confidence for an UNKNOWN abstention
        - Never send data to a cloud endpoint
        - Never bypass the epistemic boundary directive

#C Adapted from sovereign_sockets.brain_interface and nousclaww.llm_interface
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sovereign_sockets.brain_interface import BrainInterface, BrainResponse

logger = logging.getLogger(__name__)


class LocalOllamaBrain(BrainInterface):
    """BrainInterface implementation backed by a local Ollama model.

    Wraps :class:`nousclaww.llm_interface.LLMInterface` to satisfy the
    Brain socket contract. The Ollama server must be running on the
    user's machine (``ollama serve``).

    Args:
        model: Ollama model name (e.g. ``"qwen3:30b"``).
        host: Ollama server URL (default ``http://localhost:11434``).
        temperature: Sampling temperature (0.0 = precise, 0.7 = balanced).
        max_tokens: Maximum response length.
        llm: Optional pre-constructed ``LLMInterface`` instance. If
            provided, ``model``/``host``/``temperature``/``max_tokens``
            are ignored. Useful for testing with a mock.

    Raises:
        ImportError: If the ``ollama`` package is not installed and no
            pre-constructed ``llm`` is provided.
    """

    def __init__(
        self,
        model: str = "qwen3:30b",
        host: str = "http://localhost:11434",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        llm: Any | None = None,
    ) -> None:
        if llm is not None:
            self._llm = llm
        else:
            # Import lazily so the module loads without ollama installed.
            from nousclaww.llm_interface import LLMInterface
            self._llm = LLMInterface(
                model=model,
                host=host,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        self._void_sockets: list[str] = []
        self._model = model

    # ── BrainInterface ────────────────────────────────────────────────────

    def reason(
        self,
        context: str,
        query: str,
        constraints: dict[str, Any] | None = None,
    ) -> BrainResponse:
        """Generate a response from the local Ollama model.

        If the LLM call fails, returns an empty output with a void socket
        (honest silence over fabricated success). If the LLM abstains with
        an UNKNOWN prefix, confidence is 0.0 and a void socket is logged.
        """
        constraints = constraints or {}
        void_sockets: list[str] = []
        metadata: dict[str, Any] = {"model": self._model}

        try:
            output = self._llm.ask(query, context=context if context else None)
        except Exception as exc:
            logger.error("LocalOllamaBrain.reason failed: %s", exc)
            vs = self.create_void_socket(query, f"LLM error: {exc}")
            void_sockets.append(vs)
            return BrainResponse(
                output="",
                confidence=0.0,
                void_sockets=void_sockets,
                metadata={**metadata, "error": str(exc)},
            )

        # Detect abstention
        if output.startswith("UNKNOWN:"):
            vs = self.create_void_socket(query, output)
            void_sockets.append(vs)
            return BrainResponse(
                output="",
                confidence=0.0,
                void_sockets=void_sockets,
                metadata={**metadata, "abstained": True},
            )

        confidence = self.get_confidence(output, context)
        return BrainResponse(
            output=output,
            confidence=confidence,
            void_sockets=void_sockets,
            metadata=metadata,
        )

    def get_confidence(self, response: str, context: str) -> float:
        """Compute a heuristic confidence score.

        - 0.0 if the response is empty or an UNKNOWN abstention.
        - 0.7 if context was provided (grounded response).
        - 0.5 otherwise (free generation, less grounded).
        """
        if not response or response.startswith("UNKNOWN:"):
            return 0.0
        return 0.7 if context else 0.5

    def create_void_socket(self, query: str, gap_description: str) -> str:
        """Log an epistemic gap and return a tracking UUID."""
        vs_id = f"void-{uuid.uuid4().hex[:12]}"
        self._void_sockets.append(vs_id)
        logger.info("Void socket created: %s — %s", vs_id, gap_description)
        return vs_id

    # ── Introspection ─────────────────────────────────────────────────────

    @property
    def void_sockets(self) -> list[str]:
        """Return a copy of the void sockets logged by this brain."""
        return list(self._void_sockets)
