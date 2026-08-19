"""Lesson distillation — extract durable lessons from session transcripts.

Analyzes a sequence of session events (a transcript of what happened
during a work session) and uses the LLM to extract durable lessons:
things that went wrong, things that worked, patterns to avoid, and
rules to follow in future sessions. Unlike general consolidation (which
produces facts), lesson distillation produces actionable guidance with
an explicit "lesson" type and a severity/confidence score.

Contract:
    - Each lesson is a self-contained, actionable statement.
    - Lessons include a 'category' (success, failure, pattern, rule)
      and a 'confidence' score (0.0-1.0).
    - The LLM is called once with the full transcript (batched if the
      transcript is too long).
    - Lessons are returned as a list of dicts ready for store_memory().
    - If the LLM fails, no lessons are fabricated — an empty list is
      returned with a logged warning.

SYNTH:
    purpose: Extract durable lessons (successes, failures, patterns, rules) from session transcripts via LLM
    axioms: [evidence_over_intuition, epistemic_boundary, honest_failure_over_fake_success, iteration_is_progress, open_process]
    objective: Turn raw session experience into reusable, actionable lessons that improve future performance
    anti_patterns:
        - Fabricating lessons when the LLM returns empty or fails
        - Extracting lessons from sessions with no meaningful content
        - Producing vague lessons like "be careful" without actionable specifics
        - Losing traceability to the source session events
        - Making one LLM call per event instead of batching the transcript

#C Inspired by PMB (Project Memory Bank) sleep engine
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from nousclaww.memory.memory_manager import MemoryManager
from nousclaww.llm_router import LLMRouter

logger = logging.getLogger(__name__)


class LessonDistiller:
    """Extract durable lessons from session event transcripts.

    Usage:
        distiller = LessonDistiller()
        events = memory_manager.get_events(since=session_start)
        lessons = distiller.distill(events, llm_router)
        for lesson in lessons:
            memory_manager.store_memory(lesson)
    """

    # Maximum characters of transcript to send to the LLM in one call.
    # Transcripts longer than this are chunked and processed in batches.
    MAX_TRANSCRIPT_CHARS = 8000

    # Minimum content length for an event to be included in the transcript.
    MIN_EVENT_CONTENT_LEN = 5

    def __init__(
        self,
        default_importance: float = 0.6,
        max_lessons_per_chunk: int = 10,
    ) -> None:
        """Initialize the lesson distiller.

        Args:
            default_importance: Default importance score for extracted
                lessons (0.0-1.0). Individual lessons may override this
                based on their confidence.
            max_lessons_per_chunk: Maximum lessons to extract per
                transcript chunk. Prevents the LLM from generating an
                unbounded number of lessons.
        """
        self.default_importance = max(0.0, min(1.0, float(default_importance)))
        self.max_lessons_per_chunk = int(max_lessons_per_chunk)

    # ── Public API ─────────────────────────────────────────────────────────

    def distill(
        self,
        session_events: list[dict[str, Any]],
        llm_router: LLMRouter,
    ) -> list[dict[str, Any]]:
        """Extract durable lessons from a session transcript.

        Args:
            session_events: List of event dicts representing the session
                transcript. Each should have 'content' and optionally
                'event_type'/'type' and 'metadata'.
            llm_router: The LLMRouter to use for lesson extraction.

        Returns:
            A list of lesson dicts, each with keys:
                - 'type': always 'lesson'
                - 'content': the actionable lesson statement
                - 'subject': short topic label
                - 'importance': float 0.0-1.0
                - 'metadata': dict with 'category', 'confidence',
                  'source_event_ids', and 'extraction_method'
        """
        # Filter out events with no meaningful content
        meaningful_events = [
            e for e in session_events
            if len(e.get("content", "").strip()) >= self.MIN_EVENT_CONTENT_LEN
        ]

        if not meaningful_events:
            logger.info("LessonDistiller: no meaningful events to distill from")
            return []

        # Chunk the transcript if it's too long
        chunks = self._chunk_events(meaningful_events)

        all_lessons: list[dict[str, Any]] = []
        for chunk in chunks:
            lessons = self._distill_chunk(chunk, llm_router)
            all_lessons.extend(lessons)

        # Deduplicate lessons by content similarity
        unique_lessons = self._deduplicate_lessons(all_lessons)

        logger.info(
            f"LessonDistiller: extracted {len(unique_lessons)} lessons "
            f"from {len(meaningful_events)} events "
            f"(in {len(chunks)} chunks, {len(all_lessons)} before dedup)"
        )

        return unique_lessons

    # ── Chunking ───────────────────────────────────────────────────────────

    def _chunk_events(
        self, events: list[dict[str, Any]],
    ) -> list[list[dict[str, Any]]]:
        """Split events into chunks that fit within MAX_TRANSCRIPT_CHARS.

        Each chunk's formatted transcript stays under the character limit.
        Events are kept in order and never split across chunks.
        """
        chunks: list[list[dict[str, Any]]] = []
        current_chunk: list[dict[str, Any]] = []
        current_size = 0

        for event in events:
            formatted = self._format_event(event)
            event_size = len(formatted)

            # If a single event exceeds the limit, it gets its own chunk
            if event_size > self.MAX_TRANSCRIPT_CHARS:
                if current_chunk:
                    chunks.append(current_chunk)
                    current_chunk = []
                    current_size = 0
                chunks.append([event])
                continue

            if current_size + event_size > self.MAX_TRANSCRIPT_CHARS:
                chunks.append(current_chunk)
                current_chunk = [event]
                current_size = event_size
            else:
                current_chunk.append(event)
                current_size += event_size

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    # ── LLM extraction ─────────────────────────────────────────────────────

    def _distill_chunk(
        self,
        events: list[dict[str, Any]],
        llm_router: LLMRouter,
    ) -> list[dict[str, Any]]:
        """Extract lessons from a single chunk of events."""
        transcript = self._format_transcript(events)
        event_ids = [e.get("event_id", "") for e in events]

        system_prompt = (
            "You are a lesson distillation engine. Read the session "
            "transcript below and extract durable, actionable lessons. "
            "Each lesson must be a specific, reusable statement.\n\n"
            "Categories:\n"
            "  - failure: Something went wrong; what to avoid next time.\n"
            "  - success: Something worked well; what to repeat.\n"
            "  - pattern: A recurring behavior or condition observed.\n"
            "  - rule: A general principle derived from the events.\n\n"
            "Output format: a JSON array of objects, each with:\n"
            '  {"content": "<actionable lesson>", "category": "<failure|success|pattern|rule>", "confidence": <0.0-1.0>}\n\n'
            f"Extract at most {self.max_lessons_per_chunk} lessons.\n"
            "Output ONLY the JSON array. No markdown, no explanation.\n"
            "If there are no lessons to extract, output: []"
        )

        question = f"Session transcript:\n{transcript}"

        try:
            result = llm_router.ask(question, system_prompt=system_prompt)
            raw_text = result.text.strip() if result and result.text else ""
        except Exception as exc:
            logger.warning(f"LessonDistiller: LLM call failed: {exc}")
            return []

        if not raw_text:
            logger.info("LessonDistiller: LLM returned empty output")
            return []

        lessons_raw = self._parse_lessons_json(raw_text)
        if not lessons_raw:
            logger.info("LessonDistiller: no lessons parsed from LLM output")
            return []

        # Convert raw lesson dicts into memory-ready format
        lessons: list[dict[str, Any]] = []
        for raw in lessons_raw[:self.max_lessons_per_chunk]:
            content = raw.get("content", "").strip()
            if not content:
                continue
            category = raw.get("category", "pattern").strip().lower()
            if category not in ("failure", "success", "pattern", "rule"):
                category = "pattern"
            try:
                confidence = float(raw.get("confidence", 0.5))
            except (TypeError, ValueError):
                confidence = 0.5
            confidence = max(0.0, min(1.0, confidence))

            # Importance is derived from confidence and category
            importance = self._derive_importance(category, confidence)

            lessons.append({
                "type": "lesson",
                "content": content,
                "subject": self._extract_subject(content),
                "importance": importance,
                "metadata": {
                    "category": category,
                    "confidence": confidence,
                    "source_event_ids": event_ids,
                    "extraction_method": "llm_distillation",
                },
            })

        return lessons

    # ── Parsing ────────────────────────────────────────────────────────────

    def _parse_lessons_json(self, text: str) -> list[dict[str, Any]]:
        """Parse the LLM output as a JSON array of lesson objects.

        Handles common LLM formatting issues:
            - Strips markdown code fences (```json ... ```)
            - Extracts the JSON array if surrounded by other text
            - Falls back to line-by-line parsing if JSON fails
        """
        # Strip markdown code fences
        cleaned = text.strip()
        if cleaned.startswith("```"):
            # Remove opening fence (```json or ```)
            cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```\s*$", "", cleaned)

        # Try direct JSON parse first
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, list):
                return [p for p in parsed if isinstance(p, dict)]
        except json.JSONDecodeError:
            pass

        # Try to extract a JSON array from surrounding text
        match = re.search(r'\[\s*\{.*\}\s*\]', cleaned, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, list):
                    return [p for p in parsed if isinstance(p, dict)]
            except json.JSONDecodeError:
                pass

        # Fallback: try parsing as JSONL (one object per line)
        results: list[dict[str, Any]] = []
        for line in cleaned.strip().splitlines():
            line = line.strip().rstrip(",")
            if not line or line in ("[", "]"):
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    results.append(obj)
            except json.JSONDecodeError:
                continue

        return results

    # ── Deduplication ──────────────────────────────────────────────────────

    def _deduplicate_lessons(
        self, lessons: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Remove duplicate lessons by content similarity.

        Two lessons are considered duplicates if their content strings
        are identical (case-insensitive) or one is a substring of the
        other. When duplicates are found, the one with higher importance
        is kept.
        """
        if not lessons:
            return []

        seen: list[dict[str, Any]] = []
        for lesson in lessons:
            content_lower = lesson.get("content", "").lower().strip()
            is_dup = False
            for i, existing in enumerate(seen):
                existing_content = existing.get("content", "").lower().strip()
                # Exact match or one contains the other
                if (
                    content_lower == existing_content
                    or content_lower in existing_content
                    or existing_content in content_lower
                ):
                    is_dup = True
                    # Keep the higher-importance one
                    if lesson.get("importance", 0) > existing.get("importance", 0):
                        seen[i] = lesson
                    break
            if not is_dup:
                seen.append(lesson)

        return seen

    # ── Formatting helpers ──────────────────────────────────────────────────

    def _format_event(self, event: dict[str, Any]) -> str:
        """Format a single event for inclusion in the transcript."""
        etype = event.get("event_type", event.get("type", "event"))
        content = event.get("content", "").strip()
        if not content:
            return ""
        # Include metadata if it has useful context
        meta = event.get("metadata", {})
        meta_str = ""
        if isinstance(meta, dict) and meta:
            # Include up to 3 metadata key-value pairs
            items = list(meta.items())[:3]
            meta_str = " | " + ", ".join(f"{k}={v}" for k, v in items)
        return f"[{etype}] {content}{meta_str}"

    def _format_transcript(self, events: list[dict[str, Any]]) -> str:
        """Format a list of events into a numbered transcript."""
        lines: list[str] = []
        for i, event in enumerate(events, 1):
            formatted = self._format_event(event)
            if formatted:
                lines.append(f"  {i}. {formatted}")
        return "\n".join(lines) if lines else "  (empty transcript)"

    # ── Scoring helpers ─────────────────────────────────────────────────────

    def _derive_importance(self, category: str, confidence: float) -> float:
        """Derive an importance score from lesson category and confidence.

        Failures are weighted slightly higher than successes because
        avoiding mistakes is typically higher-value than repeating wins.
        """
        category_weight = {
            "failure": 1.1,
            "rule": 1.0,
            "pattern": 0.9,
            "success": 0.8,
        }.get(category, 1.0)

        importance = self.default_importance * confidence * category_weight
        return max(0.1, min(1.0, importance))

    def _extract_subject(self, content: str) -> str:
        """Extract a short subject label from lesson content.

        Takes the first few significant words as a rough subject.
        """
        words = content.split()
        if not words:
            return ""
        # Take up to 4 words as subject
        subject = " ".join(words[:4])
        # Strip trailing punctuation
        subject = re.sub(r'[.,;:]+$', "", subject)
        return subject
