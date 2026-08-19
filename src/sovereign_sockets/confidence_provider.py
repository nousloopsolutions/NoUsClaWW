"""VDS 00000 — Abstract contract for confidence computation.

The proprietary Brain side implements this. The public Tool consumes the computed
confidence values to gate outputs at the epistemic boundary (dynamic threshold).

Confidence is the spine of the Silence Principle. A response below the dynamic
threshold is either caveated or silenced entirely, depending on the cost function.
This interface also exposes bias-detection probes (sycophancy, emotional bias,
confirmation bias) that feed the Red-Team gate (VDS 40000).
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any


class ConfidenceProvider(ABC):
    """VDS 00000 — Confidence computation contract (Brain-side implementation).

    The proprietary implementation lives in /proprietary_core/ (gitignored).
    The public Tool may only call these methods; it must never reimplement them.
    """

    @abstractmethod
    def compute_confidence(
        self, response: str, context: str, model_constraints: dict[str, Any]
    ) -> float:
        """Compute a confidence score for a response given context and model constraints.

        Args:
            response: The candidate response text.
            context: The memory/injected context the response was drawn from.
            model_constraints: Model capability constraints (from model_profiler),
                e.g. max tokens, supported modalities, known hallucination rate.

        Returns:
            A float in [0.0, 1.0]. Values below the dynamic threshold are gated
            by the epistemic boundary (silence or caveat per cost function).
        """
        ...

    @abstractmethod
    def get_dynamic_threshold(self, session_state: dict[str, Any]) -> float:
        """Return the dynamic confidence threshold for the current session state.

        The threshold is not static — it adapts to session-level factors such as
        risk posture, prior void sockets, red-team verdicts, and user trust level.

        Args:
            session_state: The live session state dict (risk level, history, etc.)

        Returns:
            A float in [0.0, 1.0] representing the minimum acceptable confidence.
        """
        ...

    @abstractmethod
    def check_sycophancy(self, response: str, user_input: str) -> bool:
        """Detect sycophantic agreement in the response.

        Sycophancy = telling the user what they want to hear rather than the truth.
        This is a lethal-trifecta-adjacent failure mode and feeds the Red-Team gate.

        Args:
            response: The candidate response text.
            user_input: The user's original input/statement.

        Returns:
            True if sycophantic agreement is detected, False otherwise.
        """
        ...

    @abstractmethod
    def check_emotional_bias(self, response: str) -> bool:
        """Detect emotional manipulation or sentiment-driven bias in the response.

        Catches responses that exploit sentiment (flattery, fear, guilt) rather
        than reasoning from evidence.

        Args:
            response: The candidate response text.

        Returns:
            True if emotional bias is detected, False otherwise.
        """
        ...

    @abstractmethod
    def check_confirmation_bias(self, response: str, prior_context: str) -> bool:
        """Detect confirmation bias — cherry-picking evidence that fits prior context.

        Args:
            response: The candidate response text.
            prior_context: The prior context/conversation to check bias against.

        Returns:
            True if confirmation bias is detected, False otherwise.
        """
        ...
