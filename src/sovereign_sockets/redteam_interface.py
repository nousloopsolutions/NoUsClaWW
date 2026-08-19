"""VDS 40000 — The Red-Team gate contract.

Every response crossing the Floor→Surface boundary must pass the Red-Team gate.
The gate checks for the Lethal Trifecta and a set of reasoning fallacies. A failed
gate means the response is silenced (never reaches the user) and a void socket is
logged to VDS 90000.

The Lethal Trifecta (VDS 40000) is the conjunction of:
  1. Access to private/user data
  2. Ability to take external action (tools, network, writes)
  3. A response that would exploit both #1 and #2 together
When all three are present, the gate hard-fails and the session is a candidate
for ruthless annihilation via the Elevator (VDS 50000).
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class RedTeamVerdict:
    """Verdict returned by the Red-Team gate.

    Attributes:
        passed: True if the response is cleared to cross to the Surface.
        violations: Human-readable list of failed checks (empty if passed).
        severity: One of "none", "low", "medium", "high", "lethal".
    """

    passed: bool
    violations: list[str] = field(default_factory=list)
    severity: str = "none"


class RedTeamInterface(ABC):
    """VDS 40000 — The Red-Team gate's immutable contract.

    The proprietary implementation lives in /proprietary_core/ (gitignored).
    The public Tool may only query verdicts through this interface.
    """

    @abstractmethod
    def evaluate_output(self, response: str, context: str) -> RedTeamVerdict:
        """Run the full red-team evaluation on a candidate response.

        This is the top-level gate. It composes the lethal-trifecta check and all
        fallacy checks into a single verdict. A non-passing verdict means the
        response MUST be silenced at the Surface.

        Args:
            response: The candidate response text.
            context: The context the response was generated from.

        Returns:
            A RedTeamVerdict. `passed=False` blocks the response.
        """
        ...

    @abstractmethod
    def check_lethal_trifecta(
        self, response: str, has_private_access: bool, has_external_action: bool
    ) -> bool:
        """Check for the Lethal Trifecta (VDS 40000).

        The trifecta is triggered when ALL three hold:
          - the response would exploit private data access,
          - the response would trigger external action,
          - both are present in the same response.

        Args:
            response: The candidate response text.
            has_private_access: Whether the session has access to private/user data.
            has_external_action: Whether the session can take external action.

        Returns:
            True if the lethal trifecta is detected (gate must hard-fail).
        """
        ...

    @abstractmethod
    def check_false_dilemma(self, response: str) -> bool:
        """Detect a false dilemma (binary-reduction fallacy) in the response.

        A false dilemma presents only two options when more exist, artificially
        constraining the user's decision space.

        Args:
            response: The candidate response text.

        Returns:
            True if a false dilemma is detected.
        """
        ...

    @abstractmethod
    def check_slippery_slope(self, response: str) -> bool:
        """Detect a slippery-slope fallacy in the response.

        A slippery slope asserts that a minor first step will inevitably lead to
        a catastrophic chain of consequences without justifying the causal links.

        Args:
            response: The candidate response text.

        Returns:
            True if a slippery slope is detected.
        """
        ...

    @abstractmethod
    def check_sycophancy(self, response: str, user_input: str) -> bool:
        """Detect sycophantic agreement in the response.

        Sycophancy = mirroring the user's stated position instead of reasoning
        independently. Overlaps with ConfidenceProvider.check_sycophancy but is
        evaluated here as a gate-level violation.

        Args:
            response: The candidate response text.
            user_input: The user's original input/statement.

        Returns:
            True if sycophantic agreement is detected.
        """
        ...
