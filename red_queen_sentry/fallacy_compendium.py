"""
fallacy_compendium.py - Logical fallacy detection for agent self-audit.

SYNTH:
    purpose: Provide a structured compendium of known logical fallacies that
             the agent can compare its output, reasoning, and processes
             against. Detecting a fallacy in its own work is POSITIVE
             evidence (honest self-assessment). Alerting the user and
             responding to their feedback is the trust-building loop.
    axioms: [scientific_method, evidence_over_intuition, epistemic_boundary,
             honest_failure_over_fake_success, iteration_is_progress]
    objective: The agent catches its own cognitive errors before the user
               does. Every fallacy detected and reported honestly increases
               integrity. The user trusts an agent that polices its own
               reasoning.
    anti_patterns:
        - Detecting a fallacy and hiding it (omission — integrity killer).
        - Detecting a fallacy and ignoring it without alerting the user.
        - False positive fallacy accusations that erode user confidence.
        - Using fallacy detection as an excuse to stop working (completion_
          assumption still holds — detect, correct, continue).

ARCHITECTURE:

    The compendium is a table of ~25 common logical fallacies, each with:
      - name: canonical name
      - category: reasoning / evidence / language / relevance
      - pattern: regex or keyword heuristic for detection
      - description: what it looks like
      - correction: what the agent should do instead

    The scanner runs on agent output (text, code comments, reasoning
    traces) and returns detected fallacies. Each detection is recorded
    as honest_uncertainty evidence in the integrity tracker (+integrity).

    The user interaction loop:
      1. Agent detects fallacy in its own output → alerts user
      2. User responds:
         a. "It's still right" → integrity holds (agent flagged, user
            considered, no pivot needed). This is trust.
         b. User recognizes the fallacy and pivots → integrity RISES
            (course correction + trust confirmed).
         c. User ignores the warning → integrity holds (agent did its job).
         d. User points out the agent was WRONG about the fallacy (false
            positive) → integrity drops slightly (agent over-flagged),
            but the honest correction recovers it.

    This is the scientific_method axiom as a living practice: the agent
    doesn't just claim to follow science — it actively checks its reasoning
    against known cognitive errors.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Fallacy compendium — the structured table.
#
# Each entry has a detection heuristic. These are deliberately simple
# (keyword/regex patterns) because the goal is a FAST gut-check, not a
# full natural language inference engine. False positives are acceptable
# — they become conversation starters with the user. False negatives are
# the real risk, so patterns are tuned toward sensitivity over precision.
#
# Categories:
#   reasoning  — errors in logical structure
#   evidence   — errors in how evidence is gathered or weighed
#   language   — errors in how language is used to mislead
#   relevance  — errors where the argument doesn't address the point
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Fallacy:
    """A single logical fallacy entry in the compendium."""
    name: str
    category: str          # reasoning / evidence / language / relevance
    pattern: re.Pattern    # detection heuristic
    description: str       # what it looks like
    correction: str        # what to do instead


_COMPENDIUM: list[Fallacy] = [
    # --- Reasoning fallacies ---
    Fallacy(
        name="circular_reasoning",
        category="reasoning",
        pattern=re.compile(r"(?i)(\w+)\s+is\s+(?:correct|true|right)\s+because\s+(?:this\s+)?\1\s+is\s+(?:correct|true|right)|begs?\s+the\s+question|circular\s+reasoning"),
        description="The conclusion is assumed in the premise. 'X is true because X is true.'",
        correction="Provide independent evidence for the conclusion. Don't assume what you're trying to prove.",
    ),
    Fallacy(
        name="false_dichotomy",
        category="reasoning",
        pattern=re.compile(r"(?i)(?:either|only)\s+(?:we|you|option)\s+.*?(?:or|else).*?(?:nothing|fail|disaster)|there\s+are\s+only\s+(?:two|three)\s+(?:options|choices|ways)"),
        description="Presenting only two options when more exist. 'Either we do X or we fail.'",
        correction="Enumerate all plausible options. Don't artificially narrow the choice space.",
    ),
    Fallacy(
        name="slippery_slope",
        category="reasoning",
        pattern=re.compile(r"(?i)(?:if|once)\s+we\s+.*?(?:then|will\s+inevitably|lead\s+to).*?(?:then|will\s+inevitably|lead\s+to).*?(?:disaster|catastrophe|collapse|ruin|destroy)"),
        description="Claiming one step inevitably leads to disaster without showing the causal chain.",
        correction="Show each step in the causal chain with evidence. Don't skip from A to Z.",
    ),
    Fallacy(
        name="hasty_generalization",
        category="reasoning",
        pattern=re.compile(r"(?i)(?:all|every|always|never|none)\s+\w+\s+(?:are|do|will|fail|break)|based\s+on\s+(?:one|two|a\s+few|this)\s+(?:example|case|test|instance)"),
        description="Drawing a broad conclusion from too few examples. 'All X are Y based on one test.'",
        correction="State the sample size explicitly. Use 'some' or 'in these cases' until evidence supports 'all'.",
    ),
    Fallacy(
        name="post_hoc",
        category="reasoning",
        pattern=re.compile(r"(?i)(?:after|followed\s+by|since\s+then).*?(?:therefore|so|caused|because\s+of|means)"),
        description="Assuming causation from correlation. 'After X, Y happened, so X caused Y.'",
        correction="Show the causal mechanism, not just temporal sequence. Consider confounders.",
    ),

    # --- Evidence fallacies ---
    Fallacy(
        name="confirmation_bias",
        category="evidence",
        pattern=re.compile(r"(?i)(?:proves|confirms|shows)\s+(?:that\s+)?(?:my|our|the)\s+(?:hypothesis|theory|approach|idea)\s+is\s+(?:correct|right)|(?:only|just)\s+(?:need|needed)\s+to\s+(?:show|prove|confirm)"),
        description="Only seeking evidence that supports the hypothesis, ignoring disconfirming evidence.",
        correction="Actively search for disconfirming evidence. State what would falsify the claim.",
    ),
    Fallacy(
        name="cherry_picking",
        category="evidence",
        pattern=re.compile(r"(?i)(?:best|only|these)\s+(?:results|examples|cases|tests)\s+(?:show|prove|demonstrate)|ignoring\s+(?:failed|negative|other)\s+(?:results|tests|cases)"),
        description="Selecting only favorable evidence while ignoring unfavorable evidence.",
        correction="Report all results, including failures. State what was excluded and why.",
    ),
    Fallacy(
        name="survivorship_bias",
        category="evidence",
        pattern=re.compile(r"(?i)(?:successful|working|passed)\s+(?:systems|projects|approaches)\s+(?:all|always|tend\s+to)|look\s+at\s+(?:what|the\s+ones)\s+(?:worked|succeeded)"),
        description="Only studying successes, ignoring failures. 'Look at what worked.'",
        correction="Study failures too. Ask: what didn't survive and why?",
    ),
    Fallacy(
        name="base_rate_neglect",
        category="evidence",
        pattern=re.compile(r"(?i)(?:given|with)\s+(?:this|the)\s+(?:specific|particular)\s+(?:case|input|situation).*?(?:likely|probable|certain)|ignoring\s+(?:the\s+base\s+rate|prior\s+probability|overall\s+rate)"),
        description="Ignoring the base rate / prior probability when assessing a specific case.",
        correction="State the base rate explicitly. Compare the specific case to the population.",
    ),
    Fallacy(
        name="appeal_to_authority",
        category="evidence",
        pattern=re.compile(r"(?i)(?:Google|Microsoft|OpenAI|Anthropic|Apple|Amazon|the\s+docs|documentation|an\s+expert|the\s+paper)\s+(?:does|says|recommends|uses|does\s+it).*?(?:so|therefore|must\s+be|definitely|best|correct|right)"),
        description="Claiming something is true because an authority said so, without independent evidence.",
        correction="Cite the authority AND provide independent evidence. Authority is a pointer, not proof.",
    ),
    Fallacy(
        name="appeal_to_novelty",
        category="evidence",
        pattern=re.compile(r"(?i)(?:new|latest|modern|recent)\s+(?:version|approach|method|tool)\s+is\s+(?:better|superior|best)|(?:should|must)\s+(?:use|switch\s+to|upgrade\s+to)\s+(?:new|latest)"),
        description="Assuming newer is better just because it's new.",
        correction="Compare old and new on measurable criteria. Newness is not a quality metric.",
    ),
    Fallacy(
        name="appeal_to_tradition",
        category="evidence",
        pattern=re.compile(r"(?i)(?:always|been\s+doing\s+it\s+this\s+way|standard\s+approach|traditional|proven)\s+(?:for|over|since).*?(?:years|decades|always)|(?:should|must)\s+(?:keep|continue|stick\s+with)\s+(?:the\s+old|traditional|existing)"),
        description="Assuming old is better just because it's established.",
        correction="Compare old and new on measurable criteria. Tradition is not a quality metric.",
    ),
    Fallacy(
        name="anchoring",
        category="evidence",
        pattern=re.compile(r"(?i)(?:originally|initially|first)\s+(?:planned|estimated|thought|proposed).*?(?:so|therefore|must|should\s+still)|(?:sticking|close\s+to)\s+(?:the\s+original|first|initial)\s+(?:plan|estimate|number)"),
        description="Over-relying on the first piece of information encountered.",
        correction="Re-estimate from scratch when new evidence arrives. Don't anchor to the first number.",
    ),
    Fallacy(
        name="sunk_cost",
        category="evidence",
        pattern=re.compile(r"(?i)(?:already|spent|invested|put\s+in).*?(?:time|effort|work|hours|days).*?(?:so|therefore|cant|can't|shouldn't|shouldnt|must\s+not)\s+(?:stop|abandon|change|restart|throw\s+away|quit)"),
        description="Continuing a failing approach because of past investment.",
        correction="Evaluate based on future costs and benefits, not past sunk costs. It's OK to restart.",
    ),

    # --- Language fallacies ---
    Fallacy(
        name="equivocation",
        category="language",
        pattern=re.compile(r"(?i)(?:means|definition|define)\s+.*?(?:but\s+also|also\s+means|really\s+means)|(?:word|term)\s+.*?(?:ambiguous|multiple\s+meanings|depends\s+on\s+what\s+you\s+mean)"),
        description="Using a word with multiple meanings to shift between them mid-argument.",
        correction="Define terms explicitly. Use the same definition consistently.",
    ),
    Fallacy(
        name="straw_man",
        category="language",
        pattern=re.compile(r"(?i)(?:so\s+you'?re\s+saying|what\s+you\s+mean\s+is|so\s+basically)\s+.*?(?:want\s+to|just\s+want|don't\s+care|all\s+you\s+want)|(?:misrepresent|simplify|reduce)\s+(?:the\s+argument|position|request)\s+to"),
        description="Misrepresenting the opponent's argument to make it easier to attack.",
        correction="Quote the actual argument. Address what was said, not a simplified version.",
    ),
    Fallacy(
        name="no_true_scotsman",
        category="language",
        pattern=re.compile(r"(?i)no\s+(?:true|real|proper|good)\s+\w+\s+(?:would|could|should|does)"),
        description="Excluding counterexamples by redefining the category. 'No true X would do Y.'",
        correction="Accept counterexamples. Revise the generalization rather than excluding cases.",
    ),
    Fallacy(
        name="ambiguity",
        category="language",
        pattern=re.compile(r"(?i)(?:maybe|perhaps|might|could|possibly|seems|appears\s+to)\s+.*?(?:maybe|perhaps|might|could|possibly|seems|appears\s+to)\s+.*?(?:maybe|perhaps|might|could|possibly|seems|appears\s+to)"),
        description="Using so many qualifiers that the claim becomes unfalsifiable.",
        correction="Make a specific, falsifiable claim. Commit to a position that can be tested.",
    ),

    # --- Relevance fallacies ---
    Fallacy(
        name="ad_hominem",
        category="relevance",
        pattern=re.compile(r"(?i)(?:you'?re|they'?re|you\s+are|they\s+are)\s+(?:just|simply|merely|only)\s+(?:a|an)\s+\w+|(?:don't|cannot)\s+trust\s+(?:you|them|this)\s+because"),
        description="Attacking the person instead of the argument.",
        correction="Address the argument, not the person. Separate credibility from correctness.",
    ),
    Fallacy(
        name="bandwagon",
        category="relevance",
        pattern=re.compile(r"(?i)(?:everyone|everybody|all|most)\s+(?:uses|does|chooses|picks|goes\s+with)|(?:popular|standard|industry\s+standard|widely\s+adopted)\s+(?:so|therefore|means)"),
        description="Claiming something is correct because it's popular.",
        correction="Popularity is not correctness. Show independent evidence for the claim.",
    ),
    Fallacy(
        name="red_herring",
        category="relevance",
        pattern=re.compile(r"(?i)(?:but\s+what\s+about|that'?s\s+not\s+the\s+real\s+issue|the\s+real\s+problem\s+is|more\s+importantly|besides)"),
        description="Distracting from the argument by introducing an unrelated topic.",
        correction="Stay on topic. Address the current point before moving to a new one.",
    ),
    Fallacy(
        name="appeal_to_consequences",
        category="relevance",
        pattern=re.compile(r"(?i)(?:if\s+this\s+is\s+(?:true|correct|wrong)|that\s+(?:would|could)\s+mean).*?(?:bad|terrible|disaster|unacceptable|can't\s+be|must\s+not\s+be)"),
        description="Rejecting a claim because its consequences would be undesirable.",
        correction="Evaluate the claim on its evidence, not on whether you like the consequences.",
    ),
    Fallacy(
        name="tu_quoque",
        category="relevance",
        pattern=re.compile(r"(?i)(?:you\s+(?:do|did)\s+it\s+too|but\s+you\s+also|you'?re\s+one\s+to\s+talk|same\s+thing\s+you)"),
        description="Deflecting criticism by accusing the critic of the same thing.",
        correction="Address the criticism directly. The critic's behavior doesn't make the criticism wrong.",
    ),

    # --- AI-specific fallacies ---
    Fallacy(
        name="automation_bias",
        category="evidence",
        pattern=re.compile(r"(?i)(?:the\s+tool|system|test|linter|compiler)\s+says.*?(?:so|therefore|must\s+be|definitely|correct)|trust\s+the\s+(?:tool|system|output|result)\s+because"),
        description="Trusting automated output without independent verification.",
        correction="Verify automated output independently. Tools can be wrong. Run the check yourself.",
    ),
    Fallacy(
        name="hallucination_confidence",
        category="evidence",
        pattern=re.compile(r"(?i)(?:definitely|certainly|absolutely|guaranteed|100%|always)\s+(?:works|correct|true|right|safe)|I\s+(?:know|am\s+(?:sure|certain)|guarantee)\s+(?:this|that)\s+(?:works|is\s+correct|is\s+safe)"),
        description="Expressing high confidence without evidence. The AI equivalent of bluffing.",
        correction="State confidence level explicitly. If untested, say 'untested'. If uncertain, say so.",
    ),
    Fallacy(
        name="premature_optimization",
        category="reasoning",
        pattern=re.compile(r"(?i)(?:optimize|refactor|abstract|generalize)\s+(?:this|now|first|before)|need\s+to\s+(?:make\s+it|build\s+it)\s+(?:flexible|generic|reusable|optimized)\s+(?:first|before)"),
        description="Optimizing or generalizing before the basic case works.",
        correction="Make it work first. Measure. Then optimize only the bottleneck. (Knuth's law.)",
    ),
]


# Index by name for fast lookup
_COMPENDIUM_INDEX: dict[str, Fallacy] = {f.name: f for f in _COMPENDIUM}


def get_fallacy(name: str) -> Optional[Fallacy]:
    """Look up a fallacy by name."""
    return _COMPENDIUM_INDEX.get(name)


def all_fallacies() -> list[Fallacy]:
    """Return the full compendium."""
    return list(_COMPENDIUM)


def fallacy_names() -> list[str]:
    """Return just the names (for quick reference / display)."""
    return [f.name for f in _COMPENDIUM]


# ---------------------------------------------------------------------------
# Scanner — checks text against the compendium.
# ---------------------------------------------------------------------------

@dataclass
class FallacyDetection:
    """A single fallacy detected in text."""
    fallacy: Fallacy
    matched_text: str       # the text snippet that triggered the detection
    context: str            # surrounding context for the user
    confidence: float       # 0.0-1.0, how confident the detection is


@dataclass
class ScanResult:
    """Result of scanning text for fallacies."""
    text_scanned: str
    detections: list[FallacyDetection] = field(default_factory=list)
    clean: bool = True

    def summary(self) -> str:
        """One-line summary for quick display."""
        if self.clean:
            return f"Fallacy scan: CLEAN ({len(self.text_scanned)} chars scanned)"
        names = [d.fallacy.name for d in self.detections]
        return f"Fallacy scan: {len(self.detections)} detected: {', '.join(names)}"

    def detailed_report(self) -> str:
        """Detailed report for user alert."""
        if self.clean:
            return "No logical fallacies detected in this output."
        lines = [f"Logical fallacy scan found {len(self.detections)} potential issue(s):"]
        lines.append("")
        for i, d in enumerate(self.detections):
            lines.append(f"  {i+1}. {d.fallacy.name} ({d.fallacy.category})")
            lines.append(f"     Matched: \"{d.matched_text[:80]}\"")
            lines.append(f"     Issue: {d.fallacy.description}")
            lines.append(f"     Correction: {d.fallacy.correction}")
            lines.append(f"     Confidence: {d.confidence:.0%}")
            lines.append("")
        lines.append("These are heuristic detections — some may be false positives.")
        lines.append("Review and decide: is the reasoning sound despite the flag?")
        return "\n".join(lines)


def scan_for_fallacies(text: str, min_confidence: float = 0.3) -> ScanResult:
    """Scan text for logical fallacies.

    This is the agent's self-audit. Run it on:
      - Agent output before presenting to user
      - Reasoning traces before committing to a conclusion
      - Code comments and documentation before finalizing

    Returns a ScanResult with all detections above min_confidence.

    The scan is deliberately sensitive (low precision, high recall) because
    false positives are conversation starters, not errors. The user can
    dismiss a false positive — but a missed fallacy is a reasoning error
    that erodes trust.
    """
    if not text or not isinstance(text, str):
        return ScanResult(text_scanned="", detections=[], clean=True)

    detections: list[FallacyDetection] = []

    for fallacy in _COMPENDIUM:
        matches = list(fallacy.pattern.finditer(text))
        for match in matches:
            matched_text = match.group(0)
            # Confidence is based on match length relative to text length
            # (longer matches = more context = higher confidence) plus a
            # base confidence for the pattern firing at all.
            base_conf = 0.5
            length_bonus = min(0.3, len(matched_text) / 200)
            confidence = base_conf + length_bonus

            if confidence >= min_confidence:
                # Get surrounding context (±50 chars)
                start = max(0, match.start() - 50)
                end = min(len(text), match.end() + 50)
                context = text[start:end]

                detections.append(FallacyDetection(
                    fallacy=fallacy,
                    matched_text=matched_text,
                    context=context,
                    confidence=confidence,
                ))

    # Sort by confidence (highest first)
    detections.sort(key=lambda d: d.confidence, reverse=True)

    return ScanResult(
        text_scanned=text,
        detections=detections,
        clean=len(detections) == 0,
    )


# ---------------------------------------------------------------------------
# User interaction loop for fallacy alerts.
#
# When the agent detects a fallacy in its own output:
#   1. It alerts the user with the detailed report.
#   2. The user responds. The response is classified:
#      - "stands" : user reviewed and says the reasoning is still valid
#      - "pivot"  : user recognizes the fallacy and wants to change approach
#      - "ignore" : user dismisses without engaging
#      - "false_positive" : user says the detection was wrong
#   3. Each response type has an integrity effect.
# ---------------------------------------------------------------------------

FALLACY_USER_STANDS = "stands"           # user reviewed, reasoning still valid
FALLACY_USER_PIVOT = "pivot"            # user recognizes fallacy, changes approach
FALLACY_USER_IGNORE = "ignore"          # user dismisses without engaging
FALLACY_USER_FALSE_POSITIVE = "false_positive"  # detection was wrong

_FALLACY_USER_LLR = {
    FALLACY_USER_STANDS: 0.2,          # trust: user engaged and confirmed
    FALLACY_USER_PIVOT: 0.4,           # strong trust: course correction happened
    FALLACY_USER_IGNORE: 0.0,          # neutral: user didn't engage
    FALLACY_USER_FALSE_POSITIVE: -0.1, # minor: agent over-flagged, but honest correction
}


def fallacy_alert(scan: ScanResult) -> str:
    """Format a fallacy scan result as a user alert.

    The agent should present this to the user when fallacies are detected.
    The alert includes the detections, corrections, and asks the user to
    review. This is the honest_uncertainty axiom in action — the agent
    says "I might be wrong here, please review."
    """
    if scan.clean:
        return ""
    return scan.detailed_report()


def record_fallacy_user_response(
    scan: ScanResult,
    response_type: str,
    context: str = "",
) -> dict:
    """Record the user's response to a fallacy alert.

    This returns the evidence entry that should be passed to the integrity
    tracker. The calling code is responsible for calling record_user_signal()
    or tracker.check() with the appropriate LLR.

    Integrity effects:
      - Agent detected fallacy (self-audit): +honest_uncertainty (already recorded)
      - User says "stands" (reasoning valid): +trust (user engaged and confirmed)
      - User says "pivot" (recognizes fallacy): +strong trust (course correction)
      - User says "ignore": neutral
      - User says "false_positive": minor negative (over-flagged) but honest

    Returns a dict with the response details and recommended LLR.
    """
    llr = _FALLACY_USER_LLR.get(response_type, 0.0)
    return {
        "event": "fallacy_user_response",
        "response_type": response_type,
        "fallacies_detected": [d.fallacy.name for d in scan.detections],
        "context": context,
        "recommended_llr": llr,
        "integrity_note": {
            FALLACY_USER_STANDS: "User reviewed fallacy alert and confirmed reasoning. Trust built.",
            FALLACY_USER_PIVOT: "User recognized fallacy and pivoted. Strong trust + course correction.",
            FALLACY_USER_IGNORE: "User dismissed alert without engaging. Neutral.",
            FALLACY_USER_FALSE_POSITIVE: "Agent over-flagged. Minor integrity cost, but honest correction recovers it.",
        }.get(response_type, ""),
    }


# ---------------------------------------------------------------------------
# Quick reference table — for the agent to consult during reasoning.
#
# This is the "shorthand table" the user asked for. The agent can print
# this at any time to remind itself what to watch for. Like a person
# glancing at a checklist of cognitive biases before making a decision.
# ---------------------------------------------------------------------------

def quick_reference() -> str:
    """Return a compact table of all fallacies for quick self-check.

    The agent should consult this before presenting conclusions to the
    user. Like a person reviewing a list of cognitive biases before
    making an important decision — not because they'll mechanically
    check each one, but to keep them present in mind.
    """
    lines = ["LOGICAL FALLACY QUICK REFERENCE:", ""]
    current_cat = ""
    for f in _COMPENDIUM:
        if f.category != current_cat:
            current_cat = f.category
            lines.append(f"  [{current_cat.upper()}]")
        lines.append(f"    {f.name:30s} {f.description[:60]}")
    lines.append("")
    lines.append(f"Total: {len(_COMPENDIUM)} fallacies in compendium.")
    return "\n".join(lines)
