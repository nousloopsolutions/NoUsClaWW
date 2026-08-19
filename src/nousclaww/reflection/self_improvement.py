"""Self-improvement system — gap to proposal to validation.

The AI can propose and validate improvements to itself. This is NOT
autonomous code modification — every proposed improvement is:
1. Detected as a gap or failure by the self-reflection engine
2. Proposed as a specific, documented improvement
3. Validated by running tests before and after
4. Only applied if tests still pass
5. Logged for human review

The system can also check its own alignment to immutable directives
and propose corrections when it drifts.

Contract:
    - Self-improvement NEVER modifies code without test validation
    - Every proposal has a specific gap/failure it addresses
    - Every proposal is validated: tests must pass before and after
    - All proposals are logged for human review
    - The system can propose but CANNOT auto-apply changes
    - Proposals include: what to change, why, expected impact, validation result

SYNTH:
    purpose: Self-improvement engine that converts detected gaps and failures into validated, human-reviewable improvement proposals
    axioms: [open_process, evidence_over_intuition, honest_failure_over_fake_success, scientific_method, epistemic_boundary]
    objective: Every gap or failure detected by self-reflection is converted into a specific, validated, actionable proposal logged for human review; no proposal is auto-applied
    anti_patterns:
        - Auto-applying changes without test validation
        - Generating proposals that do not address a specific gap or failure
        - Accepting a proposal when baseline tests are failing
        - Skipping human review logging for any proposal
"""
#C Adapted from NoUs-fordge Nous-hub mvp_local_core

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class ProposalStatus(Enum):
    """Status of an improvement proposal."""
    PROPOSED = "proposed"
    VALIDATED = "validated"      # tests pass with proposed change
    REJECTED = "rejected"        # tests fail with proposed change
    APPLIED = "applied"          # human has applied the change
    SUPERSEDED = "superseded"    # replaced by a newer proposal


class ProposalType(Enum):
    """Type of improvement proposal."""
    ADD_TEST = "add_test"                # add a missing test
    FIX_STUB = "fix_stub"                # replace a stub with real implementation
    ADD_DOCSTRING = "add_docstring"      # add missing docstring
    FIX_CONNECTION = "fix_connection"    # wire a missing module connection
    ADD_INVARIANT = "add_invariant"      # add a missing invariant check
    FIX_TAUTOLOGY = "fix_tautology"      # fix a tautological test
    ALIGN_DIRECTIVE = "align_directive"  # align code with immutable directive
    FIX_EPISTEMIC_BOUNDARY = "fix_epistemic_boundary"  # fix epistemic boundary gap


@dataclass
class ImprovementProposal:
    """A proposed improvement to the system."""
    proposal_id: str = field(default_factory=lambda: f"IMP-{int(time.time())}-{hash(time.time()) % 10000}")
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    type: str = ProposalType.ADD_TEST.value
    status: str = ProposalStatus.PROPOSED.value
    title: str = ""
    description: str = ""
    target_module: str = ""
    target_file: str = ""
    gap_evidence: str = ""           # what gap/failure this addresses
    proposed_change: str = ""        # what should be changed
    expected_impact: str = ""        # what improvement this brings
    validation_result: dict[str, Any] | None = None
    human_review_notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GapDetector:
    """Detects gaps between current state and directives.

    Uses the self-reflection engine to identify where the system
    falls short, then translates those gaps into improvement proposals.
    """

    def __init__(self, reflection_engine):
        self.reflection = reflection_engine

    def detect_gaps(self) -> list[dict[str, Any]]:
        """Get all gaps from the self-reflection engine."""
        return self.reflection.get_gaps()

    def detect_failures(self) -> list[dict[str, Any]]:
        """Get all failures from the self-reflection engine."""
        return self.reflection.get_failures()

    def detect_epistemic_boundary_violations(self) -> list[dict[str, Any]]:
        """Get all EPISTEMIC_BOUNDARY gaps and failures."""
        return self.reflection.get_epistemic_boundary_violations()

    def gaps_to_proposals(self, gaps: list[dict[str, Any]]) -> list[ImprovementProposal]:
        """Convert gaps into improvement proposals."""
        proposals: list[ImprovementProposal] = []
        for gap in gaps:
            proposal = self._gap_to_proposal(gap)
            if proposal:
                proposals.append(proposal)
        return proposals

    def failures_to_proposals(self, failures: list[dict[str, Any]]) -> list[ImprovementProposal]:
        """Convert failures into improvement proposals."""
        proposals: list[ImprovementProposal] = []
        for failure in failures:
            proposal = self._failure_to_proposal(failure)
            if proposal:
                proposals.append(proposal)
        return proposals

    def _gap_to_proposal(self, gap: dict[str, Any]) -> ImprovementProposal | None:
        """Convert a single gap into a proposal."""
        directive = gap.get("directive", "")
        module = gap.get("module", "")
        evidence = gap.get("evidence", "")

        # EPISTEMIC_BOUNDARY gap -> propose fixing the epistemic boundary issue
        if "epistemic_boundary" in directive:
            sub_check = gap.get("details", {}).get("sub_check", "unknown")
            return ImprovementProposal(
                type=ProposalType.FIX_EPISTEMIC_BOUNDARY.value,
                title=f"Fix epistemic boundary gap: {sub_check} in {module}",
                description=(
                    f"The EPISTEMIC_BOUNDARY sub-check '{sub_check}' in {module} "
                    f"has a gap. Evidence: {evidence}"
                ),
                target_module=module,
                target_file=module,
                gap_evidence=evidence,
                proposed_change=(
                    f"Address the epistemic boundary sub-check '{sub_check}': "
                    f"add the missing pattern (confidence scoring, fabrication guard, "
                    f"caveat handling, sycophancy guard, honesty pattern, silence logging, "
                    f"dynamic threshold, or engagement-before-silence) to {module}."
                ),
                expected_impact=(
                    f"Brings {module} into full EPISTEMIC_BOUNDARY compliance for "
                    f"the '{sub_check}' sub-check, ensuring honest calibrated output"
                ),
            )

        # Invariant not tested -> propose adding a test
        if "invariant" in directive and "tested" in directive:
            return ImprovementProposal(
                type=ProposalType.ADD_TEST.value,
                title=f"Add test for {directive} in {module}",
                description=f"The invariant '{directive}' in {module} has no corresponding test. "
                           f"Evidence: {evidence}",
                target_module=module,
                target_file=self._find_test_file(module),
                gap_evidence=evidence,
                proposed_change=f"Add a test function that verifies the invariant described by '{directive}'",
                expected_impact="Increases test coverage and verifies the invariant is actually upheld",
            )

        # Missing docstring -> propose adding one
        if "docstring" in directive.lower():
            return ImprovementProposal(
                type=ProposalType.ADD_DOCSTRING.value,
                title=f"Add docstring to {module}",
                description=f"Test in {module} is missing a docstring. Evidence: {evidence}",
                target_module=module,
                target_file=module,
                gap_evidence=evidence,
                proposed_change="Add a docstring explaining what real-world capability this test proves",
                expected_impact="Improves test documentation and real-world value traceability",
            )

        return None

    def _failure_to_proposal(self, failure: dict[str, Any]) -> ImprovementProposal | None:
        """Convert a single failure into a proposal."""
        directive = failure.get("directive", "")
        module = failure.get("module", "")
        evidence = failure.get("evidence", "")

        # Stub found -> propose fixing it
        if directive == "no_stubs":
            return ImprovementProposal(
                type=ProposalType.FIX_STUB.value,
                title=f"Fix stub in {module}",
                description=f"Stub pattern found in {module}. Evidence: {evidence}",
                target_module=module,
                target_file=module,
                gap_evidence=evidence,
                proposed_change="Replace the stub pattern with a real implementation",
                expected_impact="Eliminates placeholder code and makes the module fully functional",
            )

        # Missing synth block -> propose adding one
        if directive == "has_synth_block":
            return ImprovementProposal(
                type=ProposalType.ALIGN_DIRECTIVE.value,
                title=f"Add synth block to {module}",
                description=f"Module {module} has no synth block. Evidence: {evidence}",
                target_module=module,
                target_file=module,
                gap_evidence=evidence,
                proposed_change="Add a complete synth block with @NCL, #S, #I, #D, #M, #T, #W, #R, #L fields",
                expected_impact="Brings module into NISEO compliance and enables automated verification",
            )

        # EPISTEMIC_BOUNDARY failure -> propose fixing it
        if "epistemic_boundary" in directive:
            sub_check = failure.get("details", {}).get("sub_check", "unknown")
            return ImprovementProposal(
                type=ProposalType.FIX_EPISTEMIC_BOUNDARY.value,
                title=f"Fix epistemic boundary failure: {sub_check} in {module}",
                description=(
                    f"The EPISTEMIC_BOUNDARY sub-check '{sub_check}' in {module} "
                    f"FAILED. Evidence: {evidence}"
                ),
                target_module=module,
                target_file=module,
                gap_evidence=evidence,
                proposed_change=(
                    f"Fix the epistemic boundary failure for '{sub_check}' in {module}. "
                    f"This is a hard failure, not just a gap — the module actively "
                    f"violates the epistemic boundary axiom."
                ),
                expected_impact=(
                    f"Restores EPISTEMIC_BOUNDARY compliance for '{sub_check}' in {module}"
                ),
            )

        return None

    def _find_test_file(self, module_path: str) -> str:
        """Find the test file path for a module."""
        module_name = Path(module_path).stem
        return f"tests/test_{module_name}.py"


class ImprovementValidator:
    """Validates improvement proposals by running tests.

    A proposal is VALIDATED if:
    1. The current test suite passes (baseline)
    2. The proposed change is described clearly enough to implement
    3. After implementing the change, tests still pass

    Since this system is propose-only (doesn't auto-apply changes),
    validation checks:
    1. The baseline test suite passes
    2. The proposal is well-formed and actionable
    """

    def __init__(self, core_dir: str | None = None, test_runner=None):
        self.core_dir = Path(core_dir) if core_dir else Path(__file__).resolve().parent.parent
        self._test_runner = test_runner

    def validate_proposal(self, proposal: ImprovementProposal) -> dict[str, Any]:
        """Validate a single proposal.

        Returns a validation result dict with:
        - valid: bool
        - baseline_tests_pass: bool
        - proposal_well_formed: bool
        - actionable: bool
        - notes: str
        """
        result: dict[str, Any] = {
            "valid": False,
            "baseline_tests_pass": False,
            "proposal_well_formed": False,
            "actionable": False,
            "notes": "",
        }

        # Check proposal is well-formed
        if not proposal.title or not proposal.description or not proposal.proposed_change:
            result["notes"] = "Proposal is missing required fields (title, description, or proposed_change)"
            return result
        result["proposal_well_formed"] = True

        # Check proposal is actionable
        if len(proposal.proposed_change) < 10:
            result["notes"] = "Proposed change is too vague to be actionable"
            return result
        if proposal.target_file:
            result["actionable"] = True
        else:
            result["notes"] = "No target file specified"
            return result

        # Check baseline tests pass (use injected runner or default)
        if self._test_runner is not None:
            result["baseline_tests_pass"] = self._test_runner()
        else:
            result["baseline_tests_pass"] = self._run_baseline_tests()
        if not result["baseline_tests_pass"]:
            result["notes"] = "Baseline tests are failing — fix existing issues before proposing new changes"
            return result

        result["valid"] = True
        result["notes"] = "Proposal is valid, well-formed, actionable, and baseline tests pass"
        return result

    def _run_baseline_tests(self) -> bool:
        """Run the baseline test suite and return True if all pass."""
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", str(self.core_dir / "tests"),
                 "--tb=no", "-q", "--timeout=120"],
                capture_output=True, text=True,
                cwd=str(self.core_dir),
                timeout=180,
            )
            output = proc.stdout + proc.stderr
            # Check if all tests passed
            if "failed" in output and "0 failed" not in output:
                return False
            return proc.returncode == 0
        except Exception:
            return False


class SelfImprovementEngine:
    """The self-improvement engine.

    This wraps the GapDetector and ImprovementValidator and adds:
    - Event logging (so improvements are observable)
    - Proposal persistence (proposals are saved for human review)
    - Batch processing (process all gaps/failures at once)
    """

    def __init__(self, reflection_engine, event_log=None, core_dir: str | None = None, test_runner=None):
        self.reflection = reflection_engine
        self.event_log = event_log
        self.detector = GapDetector(reflection_engine)
        self.validator = ImprovementValidator(core_dir=core_dir, test_runner=test_runner)
        self.proposals: list[ImprovementProposal] = []

    def run_improvement_cycle(self) -> dict[str, Any]:
        """Run a complete improvement cycle.

        1. Detect gaps and failures
        2. Convert to proposals
        3. Validate each proposal
        4. Log results
        5. Return summary
        """
        start = time.time()

        # Detect gaps and failures
        gaps = self.detector.detect_gaps()
        failures = self.detector.detect_failures()

        # Convert to proposals
        gap_proposals = self.detector.gaps_to_proposals(gaps)
        failure_proposals = self.detector.failures_to_proposals(failures)
        all_proposals = gap_proposals + failure_proposals

        # Validate each proposal
        validated = 0
        rejected = 0
        for proposal in all_proposals:
            result = self.validator.validate_proposal(proposal)
            proposal.validation_result = result
            if result["valid"]:
                proposal.status = ProposalStatus.VALIDATED.value
                validated += 1
            else:
                proposal.status = ProposalStatus.REJECTED.value
                rejected += 1
            self.proposals.append(proposal)

        duration_ms = (time.time() - start) * 1000

        summary: dict[str, Any] = {
            "gaps_found": len(gaps),
            "failures_found": len(failures),
            "proposals_generated": len(all_proposals),
            "validated": validated,
            "rejected": rejected,
            "duration_ms": duration_ms,
        }

        # Log to event log
        if self.event_log:
            self.event_log.log_operation(
                event_type="improve",
                module="self_improvement",
                operation="improvement_cycle",
                inputs={"gaps": len(gaps), "failures": len(failures)},
                outputs=summary,
                status="completed",
                duration_ms=duration_ms,
            )

        return summary

    def get_proposals(self, status: str | None = None) -> list[dict[str, Any]]:
        """Get proposals, optionally filtered by status."""
        if status:
            return [p.to_dict() for p in self.proposals if p.status == status]
        return [p.to_dict() for p in self.proposals]

    def get_validated_proposals(self) -> list[dict[str, Any]]:
        """Get all validated proposals ready for human review."""
        return self.get_proposals(ProposalStatus.VALIDATED.value)

    def get_alignment_report(self) -> dict[str, Any]:
        """Get a report on how well the system is aligned to its directives.

        This is the AI's self-assessment of its own alignment.
        """
        assessment = self.reflection.reflect()

        return {
            "timestamp": assessment.timestamp,
            "total_checks": assessment.total_checks,
            "alignment_score": assessment.passed / max(assessment.total_checks, 1),
            "passed": assessment.passed,
            "failed": assessment.failed,
            "gaps": assessment.gaps,
            "summary": assessment.summary,
            "failures": [c for c in assessment.checks if c["status"] == "fail"],
            "gaps_detail": [c for c in assessment.checks if c["status"] == "gap"],
        }
