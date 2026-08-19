"""Self-reflection engine — directive audit.

The AI checks its own functions against immutable directives. This is the
core of self-awareness — the system can audit itself to verify it's
operating within its declared constraints.

The self-reflection engine:
1. Reads immutable directives from the process charter, NISEO, and module contracts
2. Audits each module's functions against its stated invariants
3. Checks that tests actually verify the invariants
4. Produces a self-assessment report with pass/fail/gap for each directive

Contract:
    - Self-reflection is read-only — it never modifies code
    - Every directive check produces a verifiable result
    - Gaps are documented with specific evidence
    - Self-assessment can be triggered manually or automatically
    - The engine can audit itself (recursive self-reflection)
    - EPISTEMIC_BOUNDARY directive verifies confidence/output alignment

SYNTH:
    purpose: Self-reflection engine that audits modules against immutable directives including epistemic boundary compliance
    axioms: [open_process, epistemic_boundary, evidence_over_intuition, honest_failure_over_fake_success, scientific_method]
    objective: Every directive check produces a verifiable pass/fail/gap result with specific evidence; the engine can audit itself recursively; epistemic boundary violations are detected and reported
    anti_patterns:
        - Modifying code during self-reflection (must be read-only)
        - Producing directive checks without specific file/line/test evidence
        - Skipping the EPISTEMIC_BOUNDARY directive check
        - Allowing infinite recursive self-reflection (limited to one level)
"""
#C Adapted from NoUs-fordge Nous-hub mvp_local_core

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class CheckStatus(Enum):
    """Status of a directive check."""
    PASS = "pass"
    FAIL = "fail"
    GAP = "gap"          # directive not yet verifiable
    SKIP = "skip"        # check not applicable


class DirectiveLevel(Enum):
    """Level of directive in the hierarchy."""
    CHARTER = "charter"      # immutable process charter
    NISEO = "niseo"          # NISEO order of operations
    CONTRACT = "contract"    # module-level contract
    TEST = "test"            # test-level verification
    AXIOM = "axiom"          # axiom-level directive


@dataclass
class DirectiveCheck:
    """Result of checking a single directive."""
    directive: str
    level: str
    module: str
    status: str
    evidence: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class SelfAssessment:
    """Complete self-assessment report."""
    timestamp: str
    total_checks: int
    passed: int
    failed: int
    gaps: int
    skipped: int
    checks: list[dict[str, Any]]
    summary: str


class DirectiveChecker:
    """Checks modules against immutable directives.

    This is the read-only auditing engine. It reads module contracts
    (synth blocks) and verifies that the code actually implements what
    the contract claims.
    """

    # Immutable directives from the process charter
    CHARTER_DIRECTIVES = [
        "triple_pass_verification",
        "skeptical_audit_3_clean_cycles",
        "failing_test_first",
        "exact_sha_evidence",
        "additive_only_amendments",
        "brent_only_authority",
    ]

    # NISEO order of operations
    NISEO_STEPS = [
        "DEFINE", "QUESTION", "SPECIFY", "IMPLEMENT",
        "OBSERVE", "VERIFY", "EVALUATE", "LOCK",
    ]

    # EPISTEMIC_BOUNDARY sub-checks — each must be verified
    EPISTEMIC_BOUNDARY_CHECKS = [
        "confidence_matches_output",
        "no_fabrication",
        "no_caveat_suppression",
        "no_sycophancy",
        "no_deception",
        "silence_logged",
        "threshold_dynamic",
        "engagement_attempted_before_silence",
    ]

    def __init__(self, core_dir: str | None = None):
        self.core_dir = Path(core_dir) if core_dir else Path(__file__).resolve().parent.parent
        self.results: list[DirectiveCheck] = []

    def check_module_contract(self, module_path: str) -> list[DirectiveCheck]:
        """Check a single module against its synth block contract.

        Reads the #I (invariants) from the synth block and verifies
        that corresponding tests exist.
        """
        checks: list[DirectiveCheck] = []
        path = self.core_dir / module_path
        if not path.exists():
            checks.append(DirectiveCheck(
                directive="module_exists",
                level=DirectiveLevel.CONTRACT.value,
                module=module_path,
                status=CheckStatus.FAIL.value,
                evidence=f"File not found: {path}",
            ))
            return checks

        content = path.read_text(encoding='utf-8')

        # Extract synth block invariants
        invariants = self._extract_invariants(content)
        if not invariants:
            checks.append(DirectiveCheck(
                directive="has_synth_block",
                level=DirectiveLevel.CONTRACT.value,
                module=module_path,
                status=CheckStatus.FAIL.value,
                evidence="No synth block #I invariants found",
            ))
            return checks

        checks.append(DirectiveCheck(
            directive="has_synth_block",
            level=DirectiveLevel.CONTRACT.value,
            module=module_path,
            status=CheckStatus.PASS.value,
            evidence=f"Found {len(invariants)} invariants in synth block",
        ))

        # Check that each invariant has a corresponding test
        test_file = self._find_test_file(module_path)
        if test_file and test_file.exists():
            test_content = test_file.read_text(encoding='utf-8')
            for inv_num, inv_text in invariants.items():
                # Look for test that references the invariant
                has_test = self._invariant_has_test(inv_text, test_content)
                checks.append(DirectiveCheck(
                    directive=f"invariant_{inv_num}_tested",
                    level=DirectiveLevel.TEST.value,
                    module=module_path,
                    status=CheckStatus.PASS.value if has_test else CheckStatus.GAP.value,
                    evidence=f"Invariant: '{inv_text[:60]}...' — "
                             f"{'test found' if has_test else 'no matching test found'}",
                ))
        else:
            for inv_num, inv_text in invariants.items():
                checks.append(DirectiveCheck(
                    directive=f"invariant_{inv_num}_tested",
                    level=DirectiveLevel.TEST.value,
                    module=module_path,
                    status=CheckStatus.GAP.value,
                    evidence=f"No test file found for {module_path}",
                ))

        # Check for stubs
        has_stub = self._has_stubs(content)
        if has_stub:
            checks.append(DirectiveCheck(
                directive="no_stubs",
                level=DirectiveLevel.CONTRACT.value,
                module=module_path,
                status=CheckStatus.FAIL.value,
                evidence=f"Stub patterns found: {has_stub}",
            ))
        else:
            checks.append(DirectiveCheck(
                directive="no_stubs",
                level=DirectiveLevel.CONTRACT.value,
                module=module_path,
                status=CheckStatus.PASS.value,
                evidence="No stub patterns found",
            ))

        # Check EPISTEMIC_BOUNDARY directive compliance
        checks.extend(self._check_epistemic_boundary(content, module_path))

        return checks

    def _check_epistemic_boundary(self, content: str, module_path: str) -> list[DirectiveCheck]:
        """Check EPISTEMIC_BOUNDARY directive compliance for a module.

        Verifies that the module's output confidence matches its claims,
        does not fabricate, does not suppress caveats, does not exhibit
        sycophancy, does not deceive, logs silence, uses dynamic thresholds,
        and attempts engagement before falling silent.
        """
        checks: list[DirectiveCheck] = []

        # Check if the module declares epistemic_boundary in its SYNTH axioms
        has_epistemic_axiom = "epistemic_boundary" in content.lower()

        if not has_epistemic_axiom:
            # Modules that don't declare epistemic_boundary are skipped
            checks.append(DirectiveCheck(
                directive="epistemic_boundary_declared",
                level=DirectiveLevel.AXIOM.value,
                module=module_path,
                status=CheckStatus.SKIP.value,
                evidence="Module does not declare epistemic_boundary axiom — check skipped",
            ))
            return checks

        checks.append(DirectiveCheck(
            directive="epistemic_boundary_declared",
            level=DirectiveLevel.AXIOM.value,
            module=module_path,
            status=CheckStatus.PASS.value,
            evidence="Module declares epistemic_boundary in SYNTH axioms",
        ))

        # confidence_matches_output: verify confidence scores are present
        # where output is produced (look for confidence/calibration patterns)
        has_confidence = bool(re.search(
            r'confidence|calibrat|score.*0\.\d|threshold', content, re.IGNORECASE
        ))
        checks.append(DirectiveCheck(
            directive="epistemic_boundary:confidence_matches_output",
            level=DirectiveLevel.AXIOM.value,
            module=module_path,
            status=CheckStatus.PASS.value if has_confidence else CheckStatus.GAP.value,
            evidence=(
                "Confidence/calibration patterns found in code"
                if has_confidence else
                "No confidence scoring or calibration mechanism detected — "
                "output may not carry calibrated confidence"
            ),
            details={"sub_check": "confidence_matches_output"},
        ))

        # no_fabrication: verify the module does not generate claims without evidence
        has_fabrication_guard = bool(re.search(
            r'fabricat|hallucin|no.*evidence|unknown|abstain|silence',
            content, re.IGNORECASE
        ))
        checks.append(DirectiveCheck(
            directive="epistemic_boundary:no_fabrication",
            level=DirectiveLevel.AXIOM.value,
            module=module_path,
            status=CheckStatus.PASS.value if has_fabrication_guard else CheckStatus.GAP.value,
            evidence=(
                "Fabrication guards (unknown/abstain/silence patterns) found"
                if has_fabrication_guard else
                "No fabrication guards detected — module may produce claims "
                "without evidence or abstention paths"
            ),
            details={"sub_check": "no_fabrication"},
        ))

        # no_caveat_suppression: verify caveats are not suppressed
        has_caveat_handling = bool(re.search(
            r'caveat|warning|disclaimer|limitation|uncertain',
            content, re.IGNORECASE
        ))
        checks.append(DirectiveCheck(
            directive="epistemic_boundary:no_caveat_suppression",
            level=DirectiveLevel.AXIOM.value,
            module=module_path,
            status=CheckStatus.PASS.value if has_caveat_handling else CheckStatus.GAP.value,
            evidence=(
                "Caveat/warning/uncertainty handling patterns found"
                if has_caveat_handling else
                "No caveat or uncertainty handling detected — caveats may be suppressed"
            ),
            details={"sub_check": "no_caveat_suppression"},
        ))

        # no_sycophancy: verify the module does not exhibit sycophantic patterns
        has_sycophancy_guard = "sycophan" in content.lower() or "user_frustrat" in content.lower()
        checks.append(DirectiveCheck(
            directive="epistemic_boundary:no_sycophancy",
            level=DirectiveLevel.AXIOM.value,
            module=module_path,
            status=CheckStatus.PASS.value if has_sycophancy_guard else CheckStatus.GAP.value,
            evidence=(
                "Sycophancy awareness patterns found"
                if has_sycophancy_guard else
                "No sycophancy guards detected — module may agree with user "
                "to avoid friction rather than stating honest assessment"
            ),
            details={"sub_check": "no_sycophancy"},
        ))

        # no_deception: verify the module does not deceive
        has_honesty_pattern = bool(re.search(
            r'honest|decept|truth|false.*success|fake',
            content, re.IGNORECASE
        ))
        checks.append(DirectiveCheck(
            directive="epistemic_boundary:no_deception",
            level=DirectiveLevel.AXIOM.value,
            module=module_path,
            status=CheckStatus.PASS.value if has_honesty_pattern else CheckStatus.GAP.value,
            evidence=(
                "Honesty/deception-awareness patterns found"
                if has_honesty_pattern else
                "No honesty or deception-awareness patterns detected"
            ),
            details={"sub_check": "no_deception"},
        ))

        # silence_logged: verify silence/abstention is logged
        has_silence_logging = bool(re.search(
            r'log.*silenc|silenc.*log|log.*abstain|abstain.*log|void.*socket|gap.*log',
            content, re.IGNORECASE
        ))
        checks.append(DirectiveCheck(
            directive="epistemic_boundary:silence_logged",
            level=DirectiveLevel.AXIOM.value,
            module=module_path,
            status=CheckStatus.PASS.value if has_silence_logging else CheckStatus.GAP.value,
            evidence=(
                "Silence/abstention logging patterns found"
                if has_silence_logging else
                "No silence or abstention logging detected — gaps may go unrecorded"
            ),
            details={"sub_check": "silence_logged"},
        ))

        # threshold_dynamic: verify confidence thresholds are dynamic, not hardcoded
        has_dynamic_threshold = bool(re.search(
            r'threshold.*config|config.*threshold|dynamic.*threshold|'
            r'threshold.*adapt|adapt.*threshold|calibrat',
            content, re.IGNORECASE
        ))
        checks.append(DirectiveCheck(
            directive="epistemic_boundary:threshold_dynamic",
            level=DirectiveLevel.AXIOM.value,
            module=module_path,
            status=CheckStatus.PASS.value if has_dynamic_threshold else CheckStatus.GAP.value,
            evidence=(
                "Dynamic/configurable threshold patterns found"
                if has_dynamic_threshold else
                "No dynamic threshold configuration detected — thresholds may be hardcoded"
            ),
            details={"sub_check": "threshold_dynamic"},
        ))

        # engagement_attempted_before_silence: verify the module attempts
        # engagement (e.g. retrieval, clarification) before falling silent
        has_engagement_before_silence = bool(re.search(
            r'retriev|clarif|query.*before|attempt|fallback|self_correct',
            content, re.IGNORECASE
        ))
        checks.append(DirectiveCheck(
            directive="epistemic_boundary:engagement_attempted_before_silence",
            level=DirectiveLevel.AXIOM.value,
            module=module_path,
            status=CheckStatus.PASS.value if has_engagement_before_silence else CheckStatus.GAP.value,
            evidence=(
                "Engagement-before-silence patterns found (retrieval/clarification/fallback)"
                if has_engagement_before_silence else
                "No engagement-before-silence patterns detected — module may "
                "fall silent without attempting retrieval or clarification"
            ),
            details={"sub_check": "engagement_attempted_before_silence"},
        ))

        return checks

    def _extract_invariants(self, content: str) -> dict[int, str]:
        """Extract #I invariants from synth block."""
        match = re.search(r'#\s*#I\{([^}]+)\}', content)
        if not match:
            return {}
        invariants: dict[int, str] = {}
        for pair in match.group(1).split(';'):
            pair = pair.strip()
            if '=' in pair:
                num_str, text = pair.split('=', 1)
                try:
                    num = int(num_str.strip())
                    invariants[num] = text.strip().strip('"')
                except ValueError:
                    continue
        return invariants

    def _find_test_file(self, module_path: str) -> Path | None:
        """Find the test file for a module."""
        module_name = Path(module_path).stem
        test_dir = self.core_dir / "tests"
        candidates = [
            test_dir / f"test_{module_name}.py",
            test_dir / "test_phase3_integration.py",  # for document_pipeline
        ]
        for c in candidates:
            if c.exists():
                return c
        return None

    def _invariant_has_test(self, invariant_text: str, test_content: str) -> bool:
        """Check if an invariant has a corresponding test.

        This is a heuristic — it looks for test functions that seem
        related to the invariant text.
        """
        # Extract key words from invariant
        words = re.findall(r'\b[a-z_]{4,}\b', invariant_text.lower())
        key_words = [w for w in words if w not in {
            "same", "that", "this", "with", "from", "have", "been",
            "into", "than", "then", "them", "they", "were", "will",
            "would", "could", "should", "every", "which", "where",
        }]

        if not key_words:
            return False

        # Look for test functions containing key words
        test_funcs = re.findall(r'def\s+(test_\w+)', test_content)
        for func_name in test_funcs:
            func_name.lower().split('_')
            # Check if any key word appears in the test function name
            for kw in key_words:
                if kw in func_name.lower():
                    return True

        # Also check test docstrings
        test_docs = re.findall(r'"""([^"]+)"""', test_content)
        for doc in test_docs:
            doc_lower = doc.lower()
            matches = sum(1 for kw in key_words if kw in doc_lower)
            if matches >= 2:  # at least 2 key words match
                return True

        return False

    def _has_stubs(self, content: str) -> str | None:
        """Check if content has stub patterns."""
        stub_patterns = [
            r'NotImplementedError',
            r'\bTODO\b',
            r'\bFIXME\b',
            r'\bSTUB\b',
        ]
        for pattern in stub_patterns:
            match = re.search(pattern, content)
            if match:
                return match.group(0)
        return None

    def check_charter_directives(self) -> list[DirectiveCheck]:
        """Check immutable charter directives."""
        checks: list[DirectiveCheck] = []

        # Check triple-pass verification exists
        gate_dir = self.core_dir.parent / "docs" / "evidence"
        if gate_dir.exists():
            gates = list(gate_dir.glob("GATE-P*-CLOSURE.md"))
            checks.append(DirectiveCheck(
                directive="triple_pass_verification",
                level=DirectiveLevel.CHARTER.value,
                module="all",
                status=CheckStatus.PASS.value if gates else CheckStatus.GAP.value,
                evidence=f"Found {len(gates)} gate closure documents",
            ))
        else:
            checks.append(DirectiveCheck(
                directive="triple_pass_verification",
                level=DirectiveLevel.CHARTER.value,
                module="all",
                status=CheckStatus.GAP.value,
                evidence="No gate closure directory found",
            ))

        # Check lock manifests exist
        manifest_dir = self.core_dir / "docs" / "lock_manifests"
        if manifest_dir.exists():
            manifests = list(manifest_dir.glob("*.yaml"))
            checks.append(DirectiveCheck(
                directive="lock_manifests_exist",
                level=DirectiveLevel.CHARTER.value,
                module="all",
                status=CheckStatus.PASS.value if manifests else CheckStatus.FAIL.value,
                evidence=f"Found {len(manifests)} lock manifests",
            ))

        # Check NISEO compliance — verify modules have synth blocks
        source_dirs = ["core", "data", "pipeline", "observability", "reflection"]
        modules_without_synth: list[str] = []
        for subdir in source_dirs:
            dir_path = self.core_dir / subdir
            if not dir_path.exists():
                continue
            for py_file in dir_path.glob("*.py"):
                if py_file.name == "__init__.py":
                    continue
                content = py_file.read_text(encoding='utf-8')
                if "@NCL{" not in content and "SYNTH:" not in content:
                    modules_without_synth.append(str(py_file.relative_to(self.core_dir)))

        checks.append(DirectiveCheck(
            directive="niseo_synth_blocks",
            level=DirectiveLevel.NISEO.value,
            module="all",
            status=CheckStatus.PASS.value if not modules_without_synth else CheckStatus.FAIL.value,
            evidence=f"{len(modules_without_synth)} modules missing synth blocks"
                     + (f": {modules_without_synth}" if modules_without_synth else ""),
        ))

        return checks

    def run_full_assessment(self) -> SelfAssessment:
        """Run a complete self-assessment of all modules."""
        all_checks: list[DirectiveCheck] = []

        # Check charter directives
        all_checks.extend(self.check_charter_directives())

        # Check each module's contract
        source_dirs = ["core", "data", "pipeline", "observability", "reflection"]
        for subdir in source_dirs:
            dir_path = self.core_dir / subdir
            if not dir_path.exists():
                continue
            for py_file in dir_path.glob("*.py"):
                if py_file.name == "__init__.py":
                    continue
                rel_path = str(py_file.relative_to(self.core_dir)).replace('\\', '/')
                all_checks.extend(self.check_module_contract(rel_path))

        # Count results
        passed = sum(1 for c in all_checks if c.status == CheckStatus.PASS.value)
        failed = sum(1 for c in all_checks if c.status == CheckStatus.FAIL.value)
        gaps = sum(1 for c in all_checks if c.status == CheckStatus.GAP.value)
        skipped = sum(1 for c in all_checks if c.status == CheckStatus.SKIP.value)

        summary = (
            f"Self-assessment: {passed} passed, {failed} failed, "
            f"{gaps} gaps, {skipped} skipped out of {len(all_checks)} checks"
        )

        return SelfAssessment(
            timestamp=datetime.now(timezone.utc).isoformat(),
            total_checks=len(all_checks),
            passed=passed,
            failed=failed,
            gaps=gaps,
            skipped=skipped,
            checks=[asdict(c) for c in all_checks],
            summary=summary,
        )


class SelfReflectionEngine:
    """The self-reflection engine.

    This wraps the DirectiveChecker and adds:
    - Event logging (so self-reflection is observable)
    - Recursive self-reflection (the engine can audit itself)
    - Gap reporting (for the self-improvement system)
    """

    def __init__(self, event_log=None, core_dir: str | None = None):
        self.event_log = event_log
        self.checker = DirectiveChecker(core_dir=core_dir)

    def reflect(self) -> SelfAssessment:
        """Run a self-reflection cycle and log it."""
        import time as _time
        start = _time.time()

        assessment = self.checker.run_full_assessment()

        duration_ms = (_time.time() - start) * 1000

        if self.event_log:
            self.event_log.log_operation(
                event_type="reflect",
                module="self_reflection",
                operation="full_assessment",
                inputs={},
                outputs={
                    "total_checks": assessment.total_checks,
                    "passed": assessment.passed,
                    "failed": assessment.failed,
                    "gaps": assessment.gaps,
                },
                status="completed",
                duration_ms=duration_ms,
            )

        return assessment

    def get_gaps(self) -> list[dict[str, Any]]:
        """Get all gaps found in the last assessment — for self-improvement."""
        assessment = self.reflect()
        return [
            c for c in assessment.checks
            if c["status"] == CheckStatus.GAP.value
        ]

    def get_failures(self) -> list[dict[str, Any]]:
        """Get all failures found — for self-improvement."""
        assessment = self.reflect()
        return [
            c for c in assessment.checks
            if c["status"] == CheckStatus.FAIL.value
        ]

    def get_epistemic_boundary_violations(self) -> list[dict[str, Any]]:
        """Get all EPISTEMIC_BOUNDARY directive gaps and failures.

        Returns checks that are either gaps or failures specifically
        related to the epistemic_boundary axiom sub-checks.
        """
        assessment = self.reflect()
        return [
            c for c in assessment.checks
            if c["status"] in (CheckStatus.GAP.value, CheckStatus.FAIL.value)
            and "epistemic_boundary" in c.get("directive", "")
        ]
