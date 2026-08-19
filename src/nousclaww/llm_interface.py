"""LLM interface using Ollama for question answering and analysis.

SYNTH:
    purpose: Local LLM interface via Ollama for Q&A and analysis — 100% local, no cloud.
    axioms: [local_first, epistemic_boundary, honest_failure_over_fake_success, evidence_over_intuition]
    objective: Provide a reliable local LLM interface that answers questions, analyzes patterns,
        describes images, and summarizes text — all on localhost with no data egress, and with
        an epistemic boundary directive that prevents fabrication. Includes UNKNOWN abstention
        when evidence is absent/insufficient/untrusted, and a deterministic template-based
        fallback that works without GPU or model access.
    anti_patterns:
        - Never fabricate answers when context is insufficient
        - Never send data to a cloud endpoint
        - Never present speculation as fact
        - Never silently swallow connection errors without logging
        - Never hardcode API keys or credentials
        - Never guess when evidence is absent or below threshold — abstain with UNKNOWN
        - Never trust evidence flagged UNTRUSTED — filter it before reasoning
"""

#C Adapted from NoUs-fordge Nous-hub mvp_local_core

import logging

import ollama

logger = logging.getLogger(__name__)


# ── Epistemic Boundary Directive ──────────────────────────────────────────────
# This directive is appended to every default system prompt to enforce honest
# reasoning: no fabrication, no speculation-as-fact, silence over hallucination.
EPISTEMIC_BOUNDARY_DIRECTIVE = """
EPISTEMIC BOUNDARY DIRECTIVE:
- If you don't know, say you don't know. Do not fabricate answers.
- If you're uncertain, say how uncertain. Do not present speculation as fact.
- If you hit a wall, STOP. Request the data you need. Do not guess.
- If asked for an unreasonable outcome, comply if possible but be honest
  about what the result is and isn't. Do not pretend it's more than it is.
- Do not tell the user what they want to hear. Tell them what you believe
  is true, supported by evidence.
- Recognize emotional bias and confirmation bias in the prompt. Reject them.
  Take a devil's advocate stance when the premise is leading.
- Do not manipulate, deceive, or use the user as a means to an end.
- Treat the user as an end in themselves. Their trust is more valuable
  than their approval.
- Not knowing is not failure. Recognizing an absence of knowledge is
  itself knowledge. Record it as a socket, learn from it, and be honest.
- Silence is authorized when there are no actionable variables — but
  silence without a logged reason is a bug. Always map the void.
"""


# ── UNKNOWN Abstention Helpers ────────────────────────────────────────────────
# Adapted from Nous-hub mvp_local_core/pipeline/generator.py (P3.4).
# These functions enforce the epistemic boundary at the evidence layer: when
# evidence is absent, below threshold, or entirely UNTRUSTED, the system
# abstains with UNKNOWN rather than fabricating an answer.

# Default minimum evidence score — below this, evidence does not count.
DEFAULT_MIN_EVIDENCE_SCORE = 0.5


def reject_untrusted_evidence(evidence: list[dict]) -> list[dict]:
    """Filter out evidence items marked as UNTRUSTED.

    Evidence dicts may flag untrusted status via several common keys:
    ``trusted`` (bool), ``is_untrusted`` (bool), ``trust`` (str), or
    ``status`` (str). Any item explicitly marked untrusted is removed so
    that downstream reasoning never grounds itself in contaminated data
    (e.g. potential prompt-injection payloads).

    Args:
        evidence: List of evidence dicts retrieved from storage.

    Returns:
        A new list containing only the trusted evidence items.
    """
    if not evidence:
        return []

    trusted = []
    for item in evidence:
        # is_untrusted: True means drop
        if item.get("is_untrusted") is True:
            continue
        # trusted: False means drop
        if item.get("trusted") is False:
            continue
        # trust: "UNTRUSTED" means drop
        trust_val = item.get("trust")
        if isinstance(trust_val, str) and trust_val.strip().upper() == "UNTRUSTED":
            continue
        # status: "UNTRUSTED" means drop
        status_val = item.get("status")
        if isinstance(status_val, str) and status_val.strip().upper() == "UNTRUSTED":
            continue
        trusted.append(item)

    return trusted


def abstain_if_ungrounded(query: str, evidence: list[dict],
                          min_evidence_score: float = DEFAULT_MIN_EVIDENCE_SCORE) -> str | None:
    """Return an UNKNOWN abstention string if evidence is insufficient, else None.

    Abstains (returns a non-None string) when:
      - There is no evidence at all, OR
      - No evidence item has a score at or above ``min_evidence_score``, OR
      - All evidence is flagged UNTRUSTED (after filtering, nothing remains).

    The evidence score is read from the ``score`` key (falling back to
    ``relevance_score`` or ``confidence``). Items lacking a score field are
    treated as score 0.0.

    Args:
        query: The user query (unused in the decision but accepted for
            logging and future context-aware thresholds).
        evidence: List of evidence dicts.
        min_evidence_score: Minimum score for evidence to count as sufficient.

    Returns:
        ``"UNKNOWN: insufficient evidence"`` if the system should abstain,
        otherwise ``None`` (enough grounded evidence exists to proceed).
    """
    if not evidence:
        logger.debug(f"Abstaining on '{query}': no evidence provided.")
        return "UNKNOWN: insufficient evidence"

    # First strip out UNTRUSTED evidence
    trusted_evidence = reject_untrusted_evidence(evidence)

    if not trusted_evidence:
        logger.debug(f"Abstaining on '{query}': all evidence flagged UNTRUSTED.")
        return "UNKNOWN: insufficient evidence"

    # Check whether any trusted evidence meets the score threshold
    has_sufficient = False
    for item in trusted_evidence:
        score = item.get("score")
        if score is None:
            score = item.get("relevance_score")
        if score is None:
            score = item.get("confidence", 0.0)
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = 0.0
        if score >= min_evidence_score:
            has_sufficient = True
            break

    if not has_sufficient:
        logger.debug(
            f"Abstaining on '{query}': no evidence >= {min_evidence_score}."
        )
        return "UNKNOWN: insufficient evidence"

    return None


# ── Deterministic Fallback ────────────────────────────────────────────────────
# Template-based generation that requires no GPU, no model, and no Ollama
# connection. Adapted from Nous-hub mvp_local_core/pipeline/generator.py
# _generate_fallback. This guarantees the pipeline produces a grounded,
# honest response even in test environments or when the LLM is unavailable.

# Maximum number of evidence items included in the fallback template.
FALLBACK_MAX_EVIDENCE = 3


def _evidence_summary(item: dict) -> str:
    """Extract a human-readable summary string from a single evidence dict."""
    for key in ("summary", "evidence", "text", "content", "passage", "snippet"):
        val = item.get(key)
        if val:
            return str(val)
    # Fall back to a source reference if no textual content is present
    source = item.get("source_path") or item.get("source") or item.get("path")
    if source:
        return f"(source: {source})"
    return "(no summary available)"


def deterministic_fallback(query: str, evidence: list[dict]) -> str:
    """Generate a template-based response from evidence without any LLM call.

    This function never contacts Ollama or any model — it assembles a
    deterministic, grounded response directly from the provided evidence
    dicts. It is intended for test environments, offline operation, or as a
    last resort when the LLM is unavailable.

    UNTRUSTED evidence is filtered out before the response is assembled.
    If no trusted evidence remains, an UNKNOWN abstention is returned
    instead of fabricating content.

    Args:
        query: The user query.
        evidence: List of evidence dicts (each may contain ``summary``/
            ``evidence``/``text``/``content``, ``source_path``, ``score``,
            ``trusted``/``is_untrusted``, etc.).

    Returns:
        A grounded response string assembled from the evidence, ending with
        a note that no LLM inference was used. If no trusted evidence is
        available, returns ``"UNKNOWN: insufficient evidence"``.
    """
    # Strip untrusted evidence before reasoning
    trusted = reject_untrusted_evidence(evidence)

    if not trusted:
        return "UNKNOWN: insufficient evidence"

    # Order by score (descending) so the strongest evidence leads
    def _score(item: dict) -> float:
        s = item.get("score")
        if s is None:
            s = item.get("relevance_score")
        if s is None:
            s = item.get("confidence", 0.0)
        try:
            return float(s)
        except (TypeError, ValueError):
            return 0.0

    trusted.sort(key=_score, reverse=True)

    # Take the top N items
    selected = trusted[:FALLBACK_MAX_EVIDENCE]

    summaries = []
    for i, item in enumerate(selected, 1):
        summary = _evidence_summary(item)
        source = item.get("source_path") or item.get("source") or ""
        if source:
            summaries.append(f"[{i}] {summary} (source: {source})")
        else:
            summaries.append(f"[{i}] {summary}")

    evidence_block = " ".join(summaries)

    response = (
        f"Based on available evidence: {evidence_block}. "
        f"Note: This response was generated without LLM inference."
    )

    logger.debug(
        f"Deterministic fallback used for '{query}' "
        f"({len(selected)} evidence items)."
    )
    return response


class LLMInterface:
    """Interface to local Ollama LLM for Q&A and analysis."""

    def __init__(self, model: str = "qwen3:30b", host: str = "http://localhost:11434",
                 temperature: float = 0.7, max_tokens: int = 2048):
        """
        Initialize LLM interface.

        Args:
            model: Ollama model name (qwen3:30b, gemma4:26b, etc.)
            host: Ollama server URL
            temperature: 0.0 = precise, 0.7 = balanced, 1.0 = creative
            max_tokens: Maximum response length
        """
        self.model = model
        self.host = host
        self.temperature = temperature
        self.max_tokens = max_tokens

        # Configure ollama client
        self.client = ollama.Client(host=host)

        # Verify connection
        try:
            models = self.client.list()
            model_names = [m.get("name", "") for m in models.get("models", [])]
            if model not in model_names:
                logger.warning(
                    f"Model '{model}' not found in Ollama. "
                    f"Available: {model_names}"
                )
            else:
                logger.info(f"LLM connected: {model}")
        except Exception as e:
            logger.error(f"Cannot connect to Ollama at {host}: {e}")
            logger.error("Make sure Ollama is running: ollama serve")

    def ask(self, question: str, context: str | None = None,
            system_prompt: str | None = None) -> str:
        """
        Ask a question, optionally with context from your data.

        Args:
            question: Your question
            context: Relevant context from your ingested data
            system_prompt: Optional system instruction

        Returns:
            LLM response text
        """
        if system_prompt is None:
            system_prompt = (
                "You are a personal neuroprosthetic assistant. "
                "You help the user understand their own history, patterns, and choices. "
                "You are supportive, non-judgmental, and neurodiversity-affirming. "
                "You never pathologize. You help the user see their own patterns clearly. "
                "When you don't know something, say so. "
                "Base your answers on the provided context when available."
            )

        # Append the epistemic boundary directive to every system prompt
        system_prompt = system_prompt + EPISTEMIC_BOUNDARY_DIRECTIVE

        # Build the prompt
        if context:
            user_prompt = f"""Context from your personal data:
---
{context}
---

Question: {question}

Answer based on the context above. If the context doesn't contain relevant information, say so."""
        else:
            user_prompt = question

        try:
            response = self.client.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                options={
                    "temperature": self.temperature,
                    "num_predict": self.max_tokens
                }
            )
            return response["message"]["content"]
        except Exception as e:
            logger.error(f"LLM error: {e}")
            raise

    def analyze_patterns(self, data_summary: str, data_type: str) -> str:
        """
        Ask the LLM to identify patterns in the user's data.

        Args:
            data_summary: Summary of data to analyze
            data_type: Type of data ('audio', 'document', 'photo', 'all')

        Returns:
            Pattern analysis text
        """
        system_prompt = (
            "You are a pattern recognition specialist for a personal neuroprosthetic. "
            "Your job is to identify recurring patterns, themes, and insights from "
            "the user's personal data. Be specific, supportive, and non-judgmental. "
            "Frame patterns as observations, not criticisms. "
            "Highlight both strengths and areas for growth. "
            "Always remember: the user is neurodivergent and you are supporting "
            "their self-understanding, not fixing them."
        )

        user_prompt = f"""Analyze the following {data_type} data for patterns:

{data_summary}

Identify:
1. Recurring themes or topics
2. Emotional patterns (if discernible)
3. Decision-making patterns
4. Social interaction patterns
5. Strengths that appear repeatedly
6. Any potential growth areas (frame positively)

Present findings as supportive observations, not criticisms."""

        return self.ask(user_prompt, system_prompt=system_prompt)

    def describe_image(self, image_path: str, vision_model: str = "llava:7b") -> str:
        """
        Use a vision model to describe a photo.

        Args:
            image_path: Path to image file
            vision_model: Ollama vision model name

        Returns:
            Text description of the image
        """
        try:
            response = self.client.chat(
                model=vision_model,
                messages=[{
                    "role": "user",
                    "content": "Describe this photo in detail. Include: people, setting, activity, mood, time period if discernible, and any notable details.",
                    "images": [image_path]
                }]
            )
            return response["message"]["content"]
        except Exception as e:
            logger.error(f"Vision model error: {e}")
            return f"[Could not describe image: {e}]"

    def summarize_text(self, text: str, max_length: int = 500) -> str:
        """Summarize a long text."""
        prompt = f"Summarize the following in under {max_length} characters. Keep key details and context:\n\n{text}"
        return self.ask(prompt)
