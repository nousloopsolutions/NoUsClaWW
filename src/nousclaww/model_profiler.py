"""Detect loaded model, probe capabilities, and adapt features.

SYNTH:
    purpose: Detect model capabilities, adapt features, communicate constraints honestly
    axioms: [local_first, llm_agnostic, epistemic_boundary, evidence_over_intuition, honest_failure_over_fake_success]
    objective: A profiler that identifies a loaded model, probes its real capabilities via test prompts,
        maintains a registry of known models, dials back features the model cannot support, and reports
        constraints honestly to the user so expectations are calibrated.
    anti_patterns:
        - Never claim a capability without either registry evidence or a successful probe
        - Never silently enable a feature the model cannot support
        - Never hide a probe failure from the user
        - Never hardcode a single model as the only supported option
        - Never present speculation about capabilities as confirmed fact
"""
#C Inspired by PMB (Project Memory Bank) patterns

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# ── Protocols ────────────────────────────────────────────────────────────


class LLMCallable(Protocol):
    """Minimal protocol for an LLM interface that can generate responses."""

    def ask(self, question: str, context: str | None = ..., system_prompt: str | None = ...) -> str:
        """Ask a question and return the response text."""
        ...


# ── Data structures ──────────────────────────────────────────────────────


@dataclass
class ModelProfile:
    """Capability profile for a single model.

    Attributes:
        name: The model identifier as known to the backend (e.g. ``qwen2.5-coder``).
        context_window: Maximum input context length in tokens.
        supports_tool_use: Whether the model can invoke tools / function calls.
        supports_vision: Whether the model can process images.
        supports_json_mode: Whether the model can produce structured JSON output reliably.
        reasoning_depth: Qualitative reasoning depth — ``"low"``, ``"medium"``, or ``"high"``.
        max_output_tokens: Maximum number of tokens the model can generate in one response.
        probed: True if capabilities were determined by live probing rather than registry lookup.
        probe_notes: Free-text notes from probing (failures, caveats, uncertainties).
    """

    name: str = ""
    context_window: int = 4096
    supports_tool_use: bool = False
    supports_vision: bool = False
    supports_json_mode: bool = False
    reasoning_depth: str = "medium"
    max_output_tokens: int = 2048
    probed: bool = False
    probe_notes: str = ""


# ── Registry of known models ─────────────────────────────────────────────
# Keys are matched case-insensitively against the model name prefix.
# A model like "qwen2.5-coder:7b" matches the "qwen2.5-coder" entry.

_KNOWN_MODELS: dict[str, dict[str, Any]] = {
    "qwen2.5-coder": {
        "context_window": 32768,
        "supports_tool_use": True,
        "supports_vision": False,
        "supports_json_mode": True,
        "reasoning_depth": "high",
        "max_output_tokens": 8192,
    },
    "qwen3": {
        "context_window": 32768,
        "supports_tool_use": True,
        "supports_vision": False,
        "supports_json_mode": True,
        "reasoning_depth": "high",
        "max_output_tokens": 8192,
    },
    "llama3.2": {
        "context_window": 4096,
        "supports_tool_use": True,
        "supports_vision": True,
        "supports_json_mode": False,
        "reasoning_depth": "medium",
        "max_output_tokens": 2048,
    },
    "llama3.1": {
        "context_window": 131072,
        "supports_tool_use": True,
        "supports_vision": False,
        "supports_json_mode": True,
        "reasoning_depth": "high",
        "max_output_tokens": 4096,
    },
    "llama3": {
        "context_window": 8192,
        "supports_tool_use": False,
        "supports_vision": False,
        "supports_json_mode": False,
        "reasoning_depth": "medium",
        "max_output_tokens": 2048,
    },
    "gemma2": {
        "context_window": 8192,
        "supports_tool_use": False,
        "supports_vision": False,
        "supports_json_mode": True,
        "reasoning_depth": "medium",
        "max_output_tokens": 8192,
    },
    "gemma3": {
        "context_window": 131072,
        "supports_tool_use": True,
        "supports_vision": True,
        "supports_json_mode": True,
        "reasoning_depth": "high",
        "max_output_tokens": 8192,
    },
    "mistral": {
        "context_window": 32768,
        "supports_tool_use": True,
        "supports_vision": False,
        "supports_json_mode": True,
        "reasoning_depth": "high",
        "max_output_tokens": 4096,
    },
    "mixtral": {
        "context_window": 32768,
        "supports_tool_use": True,
        "supports_vision": False,
        "supports_json_mode": True,
        "reasoning_depth": "high",
        "max_output_tokens": 4096,
    },
    "phi3": {
        "context_window": 128000,
        "supports_tool_use": False,
        "supports_vision": False,
        "supports_json_mode": True,
        "reasoning_depth": "medium",
        "max_output_tokens": 2048,
    },
    "llava": {
        "context_window": 4096,
        "supports_tool_use": False,
        "supports_vision": True,
        "supports_json_mode": False,
        "reasoning_depth": "low",
        "max_output_tokens": 2048,
    },
    "command-r": {
        "context_window": 128000,
        "supports_tool_use": True,
        "supports_vision": False,
        "supports_json_mode": True,
        "reasoning_depth": "high",
        "max_output_tokens": 4096,
    },
    "deepseek-coder": {
        "context_window": 16384,
        "supports_tool_use": False,
        "supports_vision": False,
        "supports_json_mode": True,
        "reasoning_depth": "high",
        "max_output_tokens": 4096,
    },
    "deepseek-r1": {
        "context_window": 65536,
        "supports_tool_use": False,
        "supports_vision": False,
        "supports_json_mode": True,
        "reasoning_depth": "high",
        "max_output_tokens": 8192,
    },
}

# Default profile for unknown models — conservative defaults.
_DEFAULT_PROFILE: dict[str, Any] = {
    "context_window": 4096,
    "supports_tool_use": False,
    "supports_vision": False,
    "supports_json_mode": False,
    "reasoning_depth": "low",
    "max_output_tokens": 2048,
}


# ── Profiler ─────────────────────────────────────────────────────────────


class ModelProfiler:
    """Detect loaded models, probe capabilities, and adapt features.

    The profiler has two modes of operation:

    1. **Registry lookup** (:meth:`detect_model`) — matches the model name
       against a built-in registry of known models. Fast, no LLM calls.
    2. **Live probing** (:meth:`probe_capabilities`) — sends test prompts to
       the model to empirically determine what it can actually do. Slower,
       but provides evidence rather than assumptions.

    Best practice: call :meth:`detect_model` first, then :meth:`probe_capabilities`
    to verify the registry claims, then :meth:`adaptive_scale` to dial back
    features the model cannot support.
    """

    def __init__(self, registry: dict[str, dict[str, Any]] | None = None) -> None:
        """
        Initialize the profiler.

        Args:
            registry: Optional custom model registry. If provided, it is merged
                with (and takes priority over) the built-in registry.
        """
        self._registry: dict[str, dict[str, Any]] = dict(_KNOWN_MODELS)
        if registry:
            self._registry.update(registry)

    # ── Registry management ──────────────────────────────────────────────

    @property
    def registry(self) -> dict[str, dict[str, Any]]:
        """The current model registry (read-only view)."""
        return dict(self._registry)

    def register_model(self, name: str, capabilities: dict[str, Any]) -> None:
        """Add or update a model in the registry.

        Args:
            name: Model name prefix (case-insensitive matching is used).
            capabilities: Dict with keys matching :class:`ModelProfile` fields.
        """
        self._registry[name.lower()] = capabilities
        logger.info("ModelProfiler: registered model '%s'", name)

    def _lookup_registry(self, model_name: str) -> dict[str, Any] | None:
        """Look up a model in the registry by prefix match.

        Strips tag suffixes (e.g. ``:7b``, ``:latest``) and matches
        case-insensitively against registry keys.
        """
        # Strip tag: "qwen2.5-coder:7b" -> "qwen2.5-coder"
        base_name = model_name.split(":")[0].lower()
        # Direct match
        if base_name in self._registry:
            return self._registry[base_name]
        # Prefix match: "qwen2.5-coder-instruct" matches "qwen2.5-coder"
        for key, caps in self._registry.items():
            if base_name.startswith(key) or key.startswith(base_name):
                return caps
        return None

    # ── Detection ────────────────────────────────────────────────────────

    def detect_model(self, model_name: str) -> ModelProfile:
        """Detect a model's capabilities via registry lookup.

        This is a fast, no-LLM-call operation. It matches the model name
        against the known-models registry and returns a :class:`ModelProfile`.
        If the model is not in the registry, conservative defaults are used
        and ``probe_notes`` indicates the model is unrecognized.

        Args:
            model_name: The model identifier (e.g. ``qwen2.5-coder:7b``,
                ``llama3.2:3b``, or a provider ID).

        Returns:
            A :class:`ModelProfile` with registry-derived or default capabilities.
        """
        if not model_name or not model_name.strip():
            raise ValueError("model_name must be a non-empty string")

        caps = self._lookup_registry(model_name)
        if caps is not None:
            profile = ModelProfile(
                name=model_name,
                context_window=int(caps.get("context_window", _DEFAULT_PROFILE["context_window"])),
                supports_tool_use=bool(caps.get("supports_tool_use", False)),
                supports_vision=bool(caps.get("supports_vision", False)),
                supports_json_mode=bool(caps.get("supports_json_mode", False)),
                reasoning_depth=str(caps.get("reasoning_depth", "medium")),
                max_output_tokens=int(caps.get("max_output_tokens", _DEFAULT_PROFILE["max_output_tokens"])),
                probed=False,
                probe_notes=f"Detected via registry lookup (matched '{model_name.split(':')[0].lower()}').",
            )
            logger.info(
                "ModelProfiler: detected '%s' via registry — ctx=%d, tools=%s, vision=%s, json=%s",
                model_name,
                profile.context_window,
                profile.supports_tool_use,
                profile.supports_vision,
                profile.supports_json_mode,
            )
            return profile

        # Unknown model — conservative defaults
        profile = ModelProfile(
            name=model_name,
            context_window=_DEFAULT_PROFILE["context_window"],
            supports_tool_use=_DEFAULT_PROFILE["supports_tool_use"],
            supports_vision=_DEFAULT_PROFILE["supports_vision"],
            supports_json_mode=_DEFAULT_PROFILE["supports_json_mode"],
            reasoning_depth=_DEFAULT_PROFILE["reasoning_depth"],
            max_output_tokens=_DEFAULT_PROFILE["max_output_tokens"],
            probed=False,
            probe_notes=(
                f"Model '{model_name}' not in registry. Using conservative defaults. "
                "Run probe_capabilities() for empirical verification."
            ),
        )
        logger.warning(
            "ModelProfiler: '%s' not in registry — using conservative defaults",
            model_name,
        )
        return profile

    # ── Live probing ─────────────────────────────────────────────────────

    def probe_capabilities(
        self,
        model_name: str,
        llm_interface: LLMCallable | None = None,
    ) -> ModelProfile:
        """Probe a model's capabilities by sending test prompts.

        This performs live LLM calls to empirically verify:
        - **System prompt support** — does the model follow system instructions?
        - **JSON mode** — can it produce valid JSON on demand?
        - **Tool use** — can it format a tool-call response?
        - **Multi-turn reasoning** — can it reference prior context?

        If ``llm_interface`` is None, only registry-based detection is performed
        and ``probe_notes`` will indicate that live probing was skipped.

        Args:
            model_name: The model identifier to probe.
            llm_interface: An object implementing the :class:`LLMCallable` protocol
                (i.e. with an ``ask(question, context, system_prompt)`` method).
                If None, falls back to registry detection only.

        Returns:
            A :class:`ModelProfile` with ``probed=True`` if live probing occurred,
            or a registry-based profile with a note if probing was skipped.
        """
        # Start from registry detection as a baseline
        profile = self.detect_model(model_name)

        if llm_interface is None:
            profile.probe_notes = (
                "Live probing skipped (no llm_interface provided). "
                "Capabilities are registry-based only."
            )
            logger.info("ModelProfiler: probing skipped for '%s' — no interface", model_name)
            return profile

        notes: list[str] = []
        probe_failures: list[str] = []

        # ── Probe 1: System prompt support ───────────────────────────
        try:
            response = llm_interface.ask(
                question="Repeat exactly: PROBE_MARKER_42",
                system_prompt="You are a test probe. Follow instructions exactly.",
            )
            system_prompt_ok = "PROBE_MARKER_42" in response
            if not system_prompt_ok:
                probe_failures.append("system_prompt")
                notes.append("System prompt: model did not follow the marker instruction.")
            else:
                notes.append("System prompt: supported (marker echoed).")
        except Exception as e:
            system_prompt_ok = False
            probe_failures.append("system_prompt")
            notes.append(f"System prompt probe failed: {e}")

        # ── Probe 2: JSON mode ───────────────────────────────────────
        try:
            response = llm_interface.ask(
                question=(
                    "Return a JSON object with exactly two keys: "
                    '"status" set to "ok" and "count" set to 3. '
                    "Return ONLY the JSON, no other text."
                ),
                system_prompt="You are a JSON generator. Output valid JSON only.",
            )
            json_ok = self._validate_json_response(response)
            if json_ok:
                notes.append("JSON mode: supported (valid JSON returned).")
            else:
                probe_failures.append("json_mode")
                notes.append("JSON mode: model did not return valid JSON.")
        except Exception as e:
            json_ok = False
            probe_failures.append("json_mode")
            notes.append(f"JSON mode probe failed: {e}")

        # ── Probe 3: Tool use / function calling ─────────────────────
        try:
            response = llm_interface.ask(
                question=(
                    "You have access to a tool called 'get_weather' that takes "
                    'a parameter "location" (string). '
                    "The user asks: 'What is the weather in Tokyo?' "
                    "Respond by calling the tool. Format your response as: "
                    'TOOL_CALL: get_weather(location="Tokyo")'
                ),
                system_prompt="You are a tool-calling assistant.",
            )
            tool_ok = bool(re.search(r"get_weather\s*\(", response, re.IGNORECASE))
            if tool_ok:
                notes.append("Tool use: supported (tool call formatted).")
            else:
                probe_failures.append("tool_use")
                notes.append("Tool use: model did not format a tool call.")
        except Exception as e:
            tool_ok = False
            probe_failures.append("tool_use")
            notes.append(f"Tool use probe failed: {e}")

        # ── Probe 4: Multi-turn reasoning ────────────────────────────
        try:
            # First turn: establish a fact
            llm_interface.ask(
                question="Remember this number: 7382. Just acknowledge.",
                system_prompt="You are a reasoning test assistant.",
            )
            # Second turn: recall it
            response = llm_interface.ask(
                question="What number did I ask you to remember in the previous message?",
                system_prompt="You are a reasoning test assistant.",
            )
            multi_turn_ok = "7382" in response
            if multi_turn_ok:
                notes.append("Multi-turn reasoning: supported (fact recalled).")
            else:
                probe_failures.append("multi_turn")
                notes.append("Multi-turn reasoning: model could not recall prior context.")
        except Exception as e:
            multi_turn_ok = False
            probe_failures.append("multi_turn")
            notes.append(f"Multi-turn reasoning probe failed: {e}")

        # ── Assemble profile from probe results ──────────────────────
        # Probe results override registry claims (evidence over intuition)
        profile.supports_json_mode = json_ok
        profile.supports_tool_use = tool_ok
        # Vision cannot be probed via text-only interface; keep registry value
        # and note the limitation
        notes.append(
            "Vision: not probed (requires image input). "
            f"Registry value: {'supported' if profile.supports_vision else 'unsupported'}."
        )

        # Reasoning depth: infer from probe success rate
        success_count = sum([system_prompt_ok, json_ok, tool_ok, multi_turn_ok])
        if success_count >= 3:
            profile.reasoning_depth = "high"
        elif success_count >= 2:
            profile.reasoning_depth = "medium"
        else:
            profile.reasoning_depth = "low"
        notes.append(f"Reasoning depth: inferred as '{profile.reasoning_depth}' from probe success rate ({success_count}/4).")

        profile.probed = True
        profile.probe_notes = " | ".join(notes)

        if probe_failures:
            logger.warning(
                "ModelProfiler: probe of '%s' completed with failures: %s",
                model_name,
                ", ".join(probe_failures),
            )
        else:
            logger.info("ModelProfiler: probe of '%s' completed — all probes passed", model_name)

        return profile

    # ── Adaptive scaling ─────────────────────────────────────────────────

    def adaptive_scale(self, profile: ModelProfile) -> dict[str, Any]:
        """Dial back features the model cannot support.

        Returns a configuration dict that downstream systems can use to
        decide which features to enable. Every unsupported capability is
        explicitly disabled — no silent defaults.

        Args:
            profile: The :class:`ModelProfile` to scale against.

        Returns:
            A dict with keys:
                - ``enable_tool_use`` (bool)
                - ``enable_vision`` (bool)
                - ``enable_json_mode`` (bool)
                - ``enable_system_prompts`` (bool) — True unless probed otherwise
                - ``enable_multi_turn`` (bool) — True unless probed otherwise
                - ``max_context_tokens`` (int) — usable context, 90% of full window
                - ``max_output_tokens`` (int) — from profile
                - ``reasoning_depth`` (str) — from profile
                - ``scaled_back`` (list[str]) — names of features that were disabled
        """
        scaled_back: list[str] = []

        if not profile.supports_tool_use:
            scaled_back.append("tool_use")
        if not profile.supports_vision:
            scaled_back.append("vision")
        if not profile.supports_json_mode:
            scaled_back.append("json_mode")

        # System prompt and multi-turn are assumed supported unless probe notes
        # explicitly indicate failure
        enable_system_prompts = True
        enable_multi_turn = True
        if profile.probed and "system_prompt" in profile.probe_notes.lower():
            if "did not follow" in profile.probe_notes.lower() or "failed" in profile.probe_notes.lower():
                enable_system_prompts = False
                scaled_back.append("system_prompts")
        if profile.probed and "multi_turn" in profile.probe_notes.lower():
            if "could not recall" in profile.probe_notes.lower() or "failed" in profile.probe_notes.lower():
                enable_multi_turn = False
                scaled_back.append("multi_turn")

        # Reserve 10% of context window for system prompt + response overhead
        max_context = int(profile.context_window * 0.9)

        config: dict[str, Any] = {
            "enable_tool_use": profile.supports_tool_use,
            "enable_vision": profile.supports_vision,
            "enable_json_mode": profile.supports_json_mode,
            "enable_system_prompts": enable_system_prompts,
            "enable_multi_turn": enable_multi_turn,
            "max_context_tokens": max_context,
            "max_output_tokens": profile.max_output_tokens,
            "reasoning_depth": profile.reasoning_depth,
            "scaled_back": scaled_back,
        }

        if scaled_back:
            logger.info(
                "ModelProfiler: adaptive_scale for '%s' — scaled back: %s",
                profile.name,
                ", ".join(scaled_back),
            )
        else:
            logger.info("ModelProfiler: adaptive_scale for '%s' — all features enabled", profile.name)

        return config

    # ── Honest reporting ─────────────────────────────────────────────────

    def report_constraints(self, profile: ModelProfile) -> str:
        """Produce an honest, human-readable report of model constraints.

        This is intended to be shown to the user so they understand what
        the model can and cannot do. It does not sugarcoat limitations.

        Args:
            profile: The :class:`ModelProfile` to report on.

        Returns:
            A multi-line string summarizing capabilities, limitations,
            and the evidence source (registry vs. probe).
        """
        lines: list[str] = []
        lines.append(f"Model: {profile.name}")
        lines.append(f"  Context window: {profile.context_window:,} tokens")
        lines.append(f"  Max output: {profile.max_output_tokens:,} tokens")
        lines.append(f"  Reasoning depth: {profile.reasoning_depth}")
        lines.append("")
        lines.append("Capabilities:")
        lines.append(f"  Tool use:    {'YES' if profile.supports_tool_use else 'NO'}")
        lines.append(f"  Vision:      {'YES' if profile.supports_vision else 'NO'}")
        lines.append(f"  JSON mode:   {'YES' if profile.supports_json_mode else 'NO'}")
        lines.append("")
        lines.append(f"Evidence source: {'live probe' if profile.probed else 'registry lookup'}")

        if profile.probed:
            lines.append("")
            lines.append("Probe details:")
            # Split probe_notes on the delimiter we used
            for note in profile.probe_notes.split(" | "):
                lines.append(f"  - {note}")
        elif profile.probe_notes:
            lines.append("")
            lines.append(f"Notes: {profile.probe_notes}")

        # Adaptive scale summary
        config = self.adaptive_scale(profile)
        if config["scaled_back"]:
            lines.append("")
            lines.append("Features scaled back (not supported):")
            for feature in config["scaled_back"]:
                lines.append(f"  - {feature}")
        else:
            lines.append("")
            lines.append("All features enabled — no scaling back required.")

        return "\n".join(lines)

    # ── Serialization ────────────────────────────────────────────────────

    def profile_to_dict(self, profile: ModelProfile) -> dict[str, Any]:
        """Convert a :class:`ModelProfile` to a plain dict for serialization.

        Args:
            profile: The profile to serialize.

        Returns:
            A JSON-serializable dict.
        """
        return asdict(profile)

    def profile_to_json(self, profile: ModelProfile) -> str:
        """Serialize a :class:`ModelProfile` to a JSON string.

        Args:
            profile: The profile to serialize.

        Returns:
            A JSON string.
        """
        return json.dumps(asdict(profile), indent=2)

    # ── Internal helpers ─────────────────────────────────────────────────

    @staticmethod
    def _validate_json_response(response: str) -> bool:
        """Check whether a response contains valid JSON.

        Attempts to extract and parse a JSON object from the response,
        tolerating surrounding markdown fences or extra text.

        Args:
            response: The raw LLM response text.

        Returns:
            True if valid JSON was found and parsed, False otherwise.
        """
        text = response.strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            # Remove opening fence (```json or ```)
            text = re.sub(r"^```(?:json)?\s*\n?", "", text)
            text = re.sub(r"\n?```\s*$", "", text)
            text = text.strip()

        # Try direct parse
        try:
            parsed = json.loads(text)
            return isinstance(parsed, dict)
        except json.JSONDecodeError:
            pass

        # Try to extract the first {...} block
        match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
                return isinstance(parsed, dict)
            except json.JSONDecodeError:
                pass

        return False

    # ── Diagnostics ──────────────────────────────────────────────────────

    def list_known_models(self) -> list[str]:
        """Return a sorted list of all registered model name prefixes."""
        return sorted(self._registry.keys())

    def __repr__(self) -> str:
        return f"ModelProfiler(registry_size={len(self._registry)})"
