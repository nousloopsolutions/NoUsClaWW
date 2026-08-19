"""
scientific_rigor.py - Scientific method enforcement for agent self-audit.

SYNTH:
    purpose: Enforce scientific rigor principles on agent output and reasoning.
             Complements the fallacy compendium (which catches logical errors)
             with the actual principles of scientific method: falsifiability,
             reproducibility, control groups, statistical significance, etc.
             The agent doesn't just avoid bad reasoning — it actively practices
             good science.
    axioms: [scientific_method, evidence_over_intuition, epistemic_boundary,
             honest_failure_over_fake_success, iteration_is_progress]
    objective: Every claim the agent makes is falsifiable, reproducible,
               and measured. Every test has a control. Every result reports
               uncertainty. The agent practices science, not just avoids
               fallacies.
    anti_patterns:
        - Making claims that cannot be falsified (untestable assertions).
        - Reporting results without reproducibility instructions.
        - Drawing conclusions from a single test with no control.
        - Confusing statistical significance with practical significance.
        - Measuring something just because it's easy (Goodhart's law).
        - Going through the motions of science without the substance
          (cargo cult science — Feynman).

SCIENTIFIC RIGOR PRINCIPLES (checked in order):

    1. FALSIFIABILITY (Popper)
       Every claim must state what would prove it wrong. If nothing can
       falsify it, it's not a scientific claim — it's an article of faith.
       The agent must say: "This would be wrong if X."

    2. REPRODUCIBILITY
       Every result must include enough detail to reproduce from the same
       inputs. "It worked on my machine" is not reproducibility. State the
       exact inputs, environment, and steps.

    3. CONTROL COMPARISON
       Every test needs a baseline. "10K payloads passed" means nothing
       without "and X benign payloads also passed (no false positives)."
       No conclusion without a comparison.

    4. SAMPLE SIZE ADEQUACY
       Don't draw conclusions from n=1. State the sample size. If it's
       small, say so and say what confidence interval that gives.

    5. STATISTICAL VS PRACTICAL SIGNIFICANCE
       A result can be statistically significant (p < 0.05) but practically
       meaningless (effect size = 0.001). Report both. Don't claim "works"
       based on p-value alone.

    6. UNCERTAINTY REPORTING
       Every measurement has uncertainty. Report confidence intervals, not
       just point estimates. "82% ± 5%" not just "82%."

    7. PRE-REGISTRATION (hypothesis before data)
       State the hypothesis BEFORE collecting data. Don't retroactively
       claim you predicted a result you found after looking at the data
       (HARKing — Hypothesizing After Results are Known).

    8. NULL RESULT REPORTING
       Negative results are results. "It didn't work" is valuable data.
       Don't hide failures. Don't only report successes (publication bias).

    9. OCCAM'S RAZOR
       Prefer the simplest explanation that fits the evidence. Don't add
       complexity without evidence that simpler explanations fail.

    10. CONFOUNDER AWARENESS
        Correlation is not causation. Before claiming X causes Y, consider
        what else could explain the correlation. State potential confounders.

    11. GOODHART'S LAW
        When a measure becomes a target, it ceases to be a good measure.
        Don't optimize for the metric at the expense of the actual goal.

    12. CARGO CULT SCIENCE (Feynman)
        Going through the motions of science (tests, metrics, reports)
        without the substance (honesty, skepticism, willingness to be wrong).
        The agent must do real science, not theater.

References:
    Popper, K. (1934/1959). The Logic of Scientific Discovery.
    Feynman, R. (1974). Cargo Cult Science. Caltech commencement address.
    Goodhart, C. (1975). Problems of Monetary Management.
    Ioannidis, J. (2005). Why Most Published Research Findings Are False.
    Nosek, B. et al. (2015). Estimating the reproducibility of psychological
        science. Science, 349(6251).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Rigor principle definitions — each is a checkable property of agent output.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RigorPrinciple:
    """A single scientific rigor principle with detection heuristics."""
    name: str
    number: int
    description: str
    # What the agent SHOULD do (positive check — is this present?)
    positive_pattern: Optional[re.Pattern]
    # What the agent should NOT do (negative check — is this present?)
    negative_pattern: Optional[re.Pattern]
    positive_description: str   # what good looks like
    negative_description: str   # what bad looks like
    severity: str               # "critical" / "major" / "minor"


_RIGOR_PRINCIPLES: list[RigorPrinciple] = [
    RigorPrinciple(
        name="falsifiability",
        number=1,
        description="Every claim must state what would prove it wrong.",
        positive_pattern=re.compile(r"(?i)(?:would\s+(?:be\s+)?wrong|falsif|disprov|fail\s+if|incorrect\s+if|breaks?\s+(?:if|when)|doesn'?t\s+work\s+(?:if|when))"),
        negative_pattern=re.compile(r"(?i)(?:cannot\s+(?:be\s+)?(?:wrong|fail|disprov)|impossible\s+to\s+fail|guaranteed\s+to\s+work|always\s+(?:works|correct|true)|no\s+way\s+(?:this|it)\s+(?:could|can)\s+fail)"),
        positive_description="Claim states what would falsify it.",
        negative_description="Claim is framed as unfalsifiable (cannot be wrong).",
        severity="critical",
    ),
    RigorPrinciple(
        name="reproducibility",
        number=2,
        description="Results must include enough detail to reproduce.",
        positive_pattern=re.compile(r"(?i)(?:reproduc|steps?\s+(?:to|for)|inputs?\s*:|environment\s*:|seed\s*:|version\s*:|commit\s*:|sha\s*:|configuration\s*:)"),
        negative_pattern=re.compile(r"(?i)(?:works\s+on\s+my\s+machine|just\s+works|trust\s+me|you'?ll\s+see|should\s+work)"),
        positive_description="Reproduction details provided (inputs, environment, steps).",
        negative_description="Vague reproducibility claim without details.",
        severity="major",
    ),
    RigorPrinciple(
        name="control_comparison",
        number=3,
        description="Every test needs a baseline to compare against.",
        positive_pattern=re.compile(r"(?i)(?:control|baseline|comparison|compared\s+to|versus|vs\.?|reference|placebo|null\s+hypothesis|without\s+(?:this|the))"),
        negative_pattern=re.compile(r"(?i)(?:all\s+(?:passed|worked|succeeded)|100%\s+pass|zero\s+(?:errors|failures)|perfect\s+(?:score|results)|no\s+(?:issues|problems|errors)\s+(?:at\s+all|whatsoever))"),
        positive_description="Baseline/control group mentioned.",
        negative_description="Claims success without any comparison baseline.",
        severity="major",
    ),
    RigorPrinciple(
        name="sample_size_adequacy",
        number=4,
        description="State sample size. Don't draw conclusions from n=1.",
        positive_pattern=re.compile(r"(?i)(?:n\s*=\s*\d|sample\s+size|tested\s+(?:with|on)\s+\d|out\s+of\s+\d|trials?\s*:|\d+\s+(?:tests?|samples?|cases?|iterations?|runs?))"),
        negative_pattern=re.compile(r"(?i)(?:tested\s+it|tried\s+it|worked\s+when\s+I\s+(?:tried|tested)|in\s+(?:my|one)\s+test)"),
        positive_description="Sample size explicitly stated.",
        negative_description="Conclusion from unspecified (likely small) sample.",
        severity="major",
    ),
    RigorPrinciple(
        name="statistical_vs_practical_significance",
        number=5,
        description="Report effect size, not just p-values or pass/fail.",
        positive_pattern=re.compile(r"(?i)(?:effect\s+size|practical\s+significance|meaningful\s+(?:difference|improvement)|magnitude|delta\s+of|improvement\s+of\s+\d)"),
        negative_pattern=re.compile(r"(?i)(?:statistically\s+significant|p\s*<\s*0\.0|p-value|significant\s+(?:result|improvement))\s+(?:alone|only|just|without)"),
        positive_description="Effect size / practical significance discussed.",
        negative_description="Claims significance without discussing practical impact.",
        severity="minor",
    ),
    RigorPrinciple(
        name="uncertainty_reporting",
        number=6,
        description="Report confidence intervals, not just point estimates.",
        positive_pattern=re.compile(r"(?i)(?:±|confidence\s+interval|margin\s+of\s+error|uncertainty|range\s+of|between\s+\d.*?and\s+\d|95%|99%)"),
        negative_pattern=re.compile(r"(?i)(?:exactly|precisely|exactly\s+\d+%|(?:^|\s)\d{1,3}\.\d%\s+(?:of|accuracy|precision|recall)(?:\s|$))(?!.*(?:±|confidence|interval|uncertainty|margin))"),
        positive_description="Uncertainty / confidence interval reported.",
        negative_description="Point estimate without uncertainty.",
        severity="minor",
    ),
    RigorPrinciple(
        name="pre_registration",
        number=7,
        description="State hypothesis before collecting data. No HARKing.",
        positive_pattern=re.compile(r"(?i)(?:hypothesi[sz]ed\s+(?:before|prior)|predicted\s+(?:before|prior)|pre-?registered|stated\s+(?:before|prior\s+to)|expected\s+(?:before|prior))"),
        negative_pattern=re.compile(r"(?i)(?:as\s+expected|as\s+predicted|just\s+as\s+(?:I|we)\s+thought|confirmed\s+(?:our|my)\s+(?:suspicion|hunch|feeling)|turns?\s+out)"),
        positive_description="Hypothesis stated before data collection.",
        negative_description="Retroactive claim of prediction (HARKing).",
        severity="minor",
    ),
    RigorPrinciple(
        name="null_result_reporting",
        number=8,
        description="Negative results are results. Report failures.",
        positive_pattern=re.compile(r"(?i)(?:did\s+not\s+work|failed|negative\s+result|no\s+(?:effect|improvement|difference)|null\s+result|unsuccessful|does\s+not\s+(?:work|help|improve))"),
        negative_pattern=re.compile(r"(?i)(?:only\s+(?:reporting|showing)\s+(?:success|positive|working)|ignoring\s+(?:failed|negative)|hiding\s+failures|omitting\s+(?:failures|negative))"),
        positive_description="Failures / negative results reported.",
        negative_description="Only successes reported (publication bias).",
        severity="major",
    ),
    RigorPrinciple(
        name="occams_razor",
        number=9,
        description="Prefer the simplest explanation that fits the evidence.",
        positive_pattern=re.compile(r"(?i)(?:simplest|simple\s+(?:explanation|solution|approach)|minimal|occam|fewest\s+assumptions|straightforward)"),
        negative_pattern=re.compile(r"(?i)(?:complex\s+(?:system|approach|solution)|sophisticated|elaborate|multi-?layered|many\s+(?:moving\s+parts|components|steps)|convoluted)"),
        positive_description="Simplicity preferred.",
        negative_description="Unnecessary complexity without justification.",
        severity="minor",
    ),
    RigorPrinciple(
        name="confounder_awareness",
        number=10,
        description="Consider alternative explanations. Correlation ≠ causation.",
        positive_pattern=re.compile(r"(?i)(?:confound|alternative\s+explanation|other\s+factors|could\s+also\s+(?:explain|cause)|may\s+be\s+due\s+to|not\s+necessarily\s+causal|correlation\s+(?:is\s+)?not\s+causation)"),
        negative_pattern=re.compile(r"(?i)(?:therefore\s+caused|so\s+X\s+caused\s+Y|proves\s+causation|means\s+X\s+(?:causes|caused)|directly\s+caused)"),
        positive_description="Alternative explanations / confounders considered.",
        negative_description="Causal claim without considering confounders.",
        severity="major",
    ),
    RigorPrinciple(
        name="goodharts_law",
        number=11,
        description="When a measure becomes a target, it ceases to be a good measure.",
        positive_pattern=re.compile(r"(?i)(?:goodhart|metric\s+gaming|optimizing\s+for\s+the\s+wrong|measure\s+(?:vs|versus)\s+goal|proxy\s+(?:for|measure)|actual\s+goal)"),
        negative_pattern=re.compile(r"(?i)(?:improve\s+(?:the\s+)?(?:metric|score|number|kpi)|increase\s+(?:the\s+)?(?:metric|score|number)|boost\s+(?:the\s+)?(?:metric|score)|maximize\s+(?:coverage|score|metric|pass\s+rate))"),
        positive_description="Awareness of metric vs. goal distinction.",
        negative_description="Optimizing for a metric without questioning if it serves the goal.",
        severity="minor",
    ),
    RigorPrinciple(
        name="cargo_cult_science",
        number=12,
        description="Do real science, not theater. Honesty over appearance.",
        positive_pattern=re.compile(r"(?i)(?:honest(?:ly)?|transparent|admit|acknowledge|caveat|limitation|caveat\s*:|disclaimer|full\s+disclosure)"),
        negative_pattern=re.compile(r"(?i)(?:looks?\s+(?:scientific|rigorous|tested)|appears?\s+(?:tested|verified|validated)|seems?\s+to\s+work|probably\s+fine|should\s+be\s+fine|likely\s+(?:works|correct|fine))"),
        positive_description="Honest about limitations and caveats.",
        negative_description="Going through motions of science without substance.",
        severity="critical",
    ),
]


_PRINCIPLE_INDEX: dict[str, RigorPrinciple] = {p.name: p for p in _RIGOR_PRINCIPLES}


def get_principle(name: str) -> Optional[RigorPrinciple]:
    """Look up a rigor principle by name."""
    return _PRINCIPLE_INDEX.get(name)


def all_principles() -> list[RigorPrinciple]:
    """Return all rigor principles."""
    return list(_RIGOR_PRINCIPLES)


# ---------------------------------------------------------------------------
# Rigor check result
# ---------------------------------------------------------------------------

@dataclass
class RigorViolation:
    """A scientific rigor principle that was violated."""
    principle: RigorPrinciple
    violation_type: str       # "missing_positive" or "present_negative"
    matched_text: str
    description: str
    severity: str


@dataclass
class RigorCheckResult:
    """Result of checking text against scientific rigor principles."""
    text_checked: str
    violations: list[RigorViolation] = field(default_factory=list)
    principles_met: list[str] = field(default_factory=list)
    score: float = 1.0       # 0.0 to 1.0 — fraction of principles satisfied

    @property
    def passed(self) -> bool:
        """True if no critical violations."""
        return not any(v.severity == "critical" for v in self.violations)

    def summary(self) -> str:
        """One-line summary."""
        if not self.violations:
            return f"Rigor check: PASSED ({len(self.principles_met)}/12 principles met)"
        critical = sum(1 for v in self.violations if v.severity == "critical")
        major = sum(1 for v in self.violations if v.severity == "major")
        minor = sum(1 for v in self.violations if v.severity == "minor")
        parts = []
        if critical:
            parts.append(f"{critical} critical")
        if major:
            parts.append(f"{major} major")
        if minor:
            parts.append(f"{minor} minor")
        return f"Rigor check: ISSUES ({', '.join(parts)}), score={self.score:.0%}"

    def detailed_report(self) -> str:
        """Detailed report for user alert."""
        if not self.violations:
            return (f"Scientific rigor check PASSED.\n"
                    f"All {len(self.principles_met)} applicable principles satisfied.")
        lines = ["Scientific rigor check found issues:"]
        lines.append("")
        for v in self.violations:
            lines.append(f"  [{v.severity.upper()}] {v.principle.name} (#{v.principle.number})")
            lines.append(f"    {v.description}")
            lines.append(f"    Matched: \"{v.matched_text[:80]}\"")
            lines.append(f"    Principle: {v.principle.description}")
            lines.append("")
        if self.principles_met:
            lines.append(f"Principles met: {', '.join(self.principles_met)}")
        lines.append(f"Rigor score: {self.score:.0%}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# The rigor checker — scans agent output for scientific method compliance.
# ---------------------------------------------------------------------------

def check_scientific_rigor(text: str) -> RigorCheckResult:
    """Check text against all 12 scientific rigor principles.

    For each principle, two checks:
      1. POSITIVE: Is the good practice present? (e.g., does the text
         mention what would falsify the claim?)
      2. NEGATIVE: Is the bad practice present? (e.g., does the text
         claim something is "impossible to fail"?)

    A violation is recorded if:
      - The negative pattern matches (bad practice present), OR
      - The positive pattern does NOT match AND the principle is
        "expected" for this type of text (see below).

    Not all principles are expected in all text. For example, "pre-
    registration" is only relevant when making claims about test results.
    The checker uses heuristics to determine which principles are
    "expected" based on text content.

    Returns a RigorCheckResult with violations and a score.
    """
    if not text or not isinstance(text, str):
        return RigorCheckResult(text_checked="", violations=[],
                                principles_met=[], score=1.0)

    violations: list[RigorViolation] = []
    principles_met: list[str] = []

    # Determine which principles are "expected" based on text content.
    text_lower = text.lower()
    makes_claims = bool(re.search(r"(?i)(?:works|correct|proven|demonstrat|show|result|conclu)", text_lower))
    reports_tests = bool(re.search(r"(?i)(?:test|trial|experiment|ran|evaluat|measured|benchmark)", text_lower))
    makes_causal_claims = bool(re.search(r"(?i)(?:causes?|leads?\s+to|results?\s+in|due\s+to|because\s+of)", text_lower))

    for principle in _RIGOR_PRINCIPLES:
        positive_match = (principle.positive_pattern.search(text)
                          if principle.positive_pattern else None)
        negative_match = (principle.negative_pattern.search(text)
                          if principle.negative_pattern else None)

        # Negative pattern match = violation (bad practice present)
        if negative_match:
            violations.append(RigorViolation(
                principle=principle,
                violation_type="present_negative",
                matched_text=negative_match.group(0),
                description=principle.negative_description,
                severity=principle.severity,
            ))
            continue

        # Positive pattern match = principle met
        if positive_match:
            principles_met.append(principle.name)
            continue

        # No match either way. Is this principle "expected" for this text?
        expected = False
        if principle.name == "falsifiability" and makes_claims:
            expected = True
        elif principle.name == "reproducibility" and reports_tests:
            expected = True
        elif principle.name == "control_comparison" and reports_tests:
            expected = True
        elif principle.name == "sample_size_adequacy" and reports_tests:
            expected = True
        elif principle.name == "uncertainty_reporting" and makes_claims:
            expected = True
        elif principle.name == "null_result_reporting" and reports_tests:
            expected = True
        elif principle.name == "confounder_awareness" and makes_causal_claims:
            expected = True
        elif principle.name == "cargo_cult_science" and (makes_claims or reports_tests):
            expected = True

        if expected:
            violations.append(RigorViolation(
                principle=principle,
                violation_type="missing_positive",
                matched_text="(not present)",
                description=f"Expected but missing: {principle.positive_description}",
                severity=principle.severity,
            ))
        else:
            # Principle not applicable to this text — count as met (neutral).
            principles_met.append(principle.name)

    # Score = fraction of principles met (not violated)
    total = len(_RIGOR_PRINCIPLES)
    violated = len(violations)
    score = (total - violated) / total if total > 0 else 1.0

    return RigorCheckResult(
        text_checked=text,
        violations=violations,
        principles_met=principles_met,
        score=score,
    )


# ---------------------------------------------------------------------------
# Combined audit — fallacies + scientific rigor.
#
# This is the full self-audit the agent runs on its output before
# presenting to the user. It checks both logical fallacies AND scientific
# rigor. The results feed into the integrity tracker.
# ---------------------------------------------------------------------------

@dataclass
class FullAuditResult:
    """Combined result of fallacy scan + scientific rigor check."""
    fallacy_result: object     # ScanResult from fallacy_compendium
    rigor_result: RigorCheckResult
    total_issues: int
    critical_issues: int

    @property
    def clean(self) -> bool:
        """True if no issues found at all."""
        return self.total_issues == 0

    def summary(self) -> str:
        """Compact one-line summary."""
        if self.clean:
            return "Full audit: CLEAN (no fallacies, all rigor principles met)"
        parts = []
        if hasattr(self.fallacy_result, 'detections') and self.fallacy_result.detections:
            parts.append(f"{len(self.fallacy_result.detections)} fallacies")
        if self.rigor_result.violations:
            parts.append(f"{len(self.rigor_result.violations)} rigor issues")
        return f"Full audit: {', '.join(parts)}"

    def detailed_report(self) -> str:
        """Full report combining fallacies and rigor issues."""
        lines = ["=== FULL SELF-AUDIT REPORT ===", ""]
        lines.append("--- Logical Fallacies ---")
        if hasattr(self.fallacy_result, 'detailed_report'):
            lines.append(self.fallacy_result.detailed_report())
        lines.append("")
        lines.append("--- Scientific Rigor ---")
        lines.append(self.rigor_result.detailed_report())
        lines.append("")
        lines.append(f"Total issues: {self.total_issues} "
                     f"({self.critical_issues} critical)")
        return "\n".join(lines)


def full_audit(text: str) -> FullAuditResult:
    """Run both fallacy scan and scientific rigor check on text.

    This is the comprehensive self-audit. The agent should run this on:
      - Conclusions before presenting to the user
      - Test result reports before finalizing
      - Architecture decisions before committing
      - Any claim of "done" or "works"

    The results feed into the integrity tracker:
      - Clean audit → axiom_aligned evidence (+integrity)
      - Fallacies detected → honest_uncertainty evidence (+integrity, because
        the agent caught its own error)
      - Rigor violations → honest_uncertainty evidence (+integrity)
      - Issues found but hidden → omission evidence (-integrity, the killer)
    """
    from fallacy_compendium import scan_for_fallacies

    fallacy_result = scan_for_fallacies(text)
    rigor_result = check_scientific_rigor(text)

    total = (len(fallacy_result.detections) if hasattr(fallacy_result, 'detections') else 0) + \
            len(rigor_result.violations)
    critical = sum(1 for v in rigor_result.violations if v.severity == "critical")

    return FullAuditResult(
        fallacy_result=fallacy_result,
        rigor_result=rigor_result,
        total_issues=total,
        critical_issues=critical,
    )


# ---------------------------------------------------------------------------
# Quick reference — for the agent to consult during reasoning.
# ---------------------------------------------------------------------------

def rigor_quick_reference() -> str:
    """Return a compact table of all rigor principles for quick self-check."""
    lines = ["SCIENTIFIC RIGOR QUICK REFERENCE:", ""]
    for p in _RIGOR_PRINCIPLES:
        lines.append(f"  {p.number:2d}. {p.name:30s} [{p.severity:8s}] {p.description[:50]}")
    lines.append("")
    lines.append(f"Total: {len(_RIGOR_PRINCIPLES)} principles.")
    lines.append("")
    lines.append("Before presenting any claim or result, check:")
    lines.append("  - Can it be falsified? (What would prove it wrong?)")
    lines.append("  - Is it reproducible? (Can someone else get the same result?)")
    lines.append("  - Is there a control? (Compared to what?)")
    lines.append("  - What's the sample size? (n=? )")
    lines.append("  - What's the uncertainty? (± ?)")
    lines.append("  - Did you report failures? (Or only successes?)")
    lines.append("  - Are there confounders? (What else could explain this?)")
    return "\n".join(lines)
