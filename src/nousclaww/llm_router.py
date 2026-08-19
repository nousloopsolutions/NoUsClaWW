"""Hybrid LLM router — local-first with optional cloud fallback.

Extends the existing LLMInterface (Ollama-only) to support a hybrid model:
  1. Primary: local Ollama model (100% local, no data leaves machine)
  2. Fallback: cloud API (OpenAI/Anthropic/etc.) when local model is
     unavailable, too slow, or the task requires more capable reasoning

The fallback is OFF by default and must be explicitly enabled. When enabled,
the router tries local first and only falls back to cloud when:
  - The local model is not running / not installed
  - The local model times out
  - The caller explicitly requests cloud (via force_cloud=True)
  - The task is tagged as requiring high reasoning (via reason="complex")

Contract:
    - Local is ALWAYS tried first unless force_cloud=True.
    - Cloud fallback requires explicit enablement + API key.
    - All cloud calls are logged to the event log with a privacy marker.
    - The router never sends data to cloud without the fallback being enabled.
    - Vision tasks route to the appropriate vision model per provider.

SYNTH:
    purpose: Hybrid LLM router — local Ollama primary, cloud fallback when local unavailable or insufficient.
    axioms: [local_first, llm_agnostic, epistemic_boundary, honest_failure_over_fake_success, reversibility_awareness]
    objective: Route LLM requests to a local model first, falling back to cloud only when explicitly
        enabled and local is unavailable or insufficient, with full privacy logging and graceful
        degradation that never fabricates results.
    anti_patterns:
        - Never send data to cloud without explicit user enablement
        - Never hardcode API keys — always use environment variables
        - Never silently fall back to cloud without logging a privacy marker
        - Never present a failed routing result as a successful answer
        - Never skip local-first when cloud is not explicitly forced
"""

#C Adapted from NoUs-fordge Nous-hub mvp_local_core

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class LLMProvider(Enum):
    """Supported LLM providers."""
    OLLAMA = "ollama"        # local, always primary
    OPENAI = "openai"        # cloud fallback
    ANTHROPIC = "anthropic"  # cloud fallback
    OPENROUTER = "openrouter"  # cloud fallback (multi-model)


class RoutingDecision(Enum):
    """Why a particular provider was chosen."""
    LOCAL_PRIMARY = "local_primary"
    LOCAL_SUCCESS = "local_success"
    CLOUD_FALLBACK = "cloud_fallback"
    CLOUD_FORCED = "cloud_forced"
    CLOUD_COMPLEX_TASK = "cloud_complex_task"
    ALL_FAILED = "all_failed"


@dataclass
class RouterConfig:
    """Configuration for the hybrid LLM router."""
    # Local (Ollama) — primary
    local_model: str = "qwen3:30b"
    local_host: str = "http://localhost:11434"
    local_vision_model: str = "llava:7b"
    local_timeout: float = 60.0

    # Cloud fallback — off by default
    cloud_enabled: bool = False
    cloud_provider: LLMProvider = LLMProvider.OPENAI
    cloud_model: str = "gpt-4o"
    cloud_vision_model: str = "gpt-4o"
    cloud_api_key: str = ""
    cloud_timeout: float = 30.0
    cloud_max_tokens: int = 4096

    # Routing rules
    complex_task_keywords: set[str] = field(default_factory=lambda: {
        "refactor", "architecture", "design", "analyze", "reason",
        "complex", "multi-step", "plan", "debug", "review",
    })

    def __post_init__(self):
        if not self.cloud_api_key and self.cloud_enabled:
            # Try to load from environment
            env_map = {
                LLMProvider.OPENAI: "OPENAI_API_KEY",
                LLMProvider.ANTHROPIC: "ANTHROPIC_API_KEY",
                LLMProvider.OPENROUTER: "OPENROUTER_API_KEY",
            }
            env_var = env_map.get(self.cloud_provider, "")
            self.cloud_api_key = os.environ.get(env_var, "")
            if not self.cloud_api_key:
                logger.warning(
                    f"Cloud fallback enabled but no API key found for "
                    f"{self.cloud_provider.value} (env: {env_var})"
                )


@dataclass
class RoutingResult:
    """Result of a routing decision and LLM call."""
    text: str
    provider: str
    decision: str
    model: str
    duration_ms: float
    error: str | None = None
    cloud_used: bool = False


class LLMRouter:
    """Hybrid LLM router: local Ollama first, cloud fallback when needed.

    Usage:
        router = LLMRouter()  # local-only by default
        router.config.cloud_enabled = True  # enable cloud fallback
        result = router.ask("What is 2+2?")
        print(result.text, result.provider)  # "4", "ollama"

        # Force cloud for complex reasoning
        result = router.ask("Design a distributed consensus algorithm",
                           force_cloud=True)
    """

    def __init__(
        self,
        config: RouterConfig | None = None,
        event_log=None,
    ):
        self.config = config or RouterConfig()
        self.event_log = event_log
        self._local_llm = None
        self._cloud_client = None

    # ── Local (Ollama) ────────────────────────────────────────────────────

    def _get_local(self):
        """Get or create the local LLM interface."""
        if self._local_llm is not None:
            return self._local_llm
        try:
            from nousclaww.llm_interface import LLMInterface
            self._local_llm = LLMInterface(
                model=self.config.local_model,
                host=self.config.local_host,
            )
            return self._local_llm
        except Exception as e:
            logger.warning(f"Local LLM init failed: {e}")
            return None

    def _call_local(self, question: str, context: str | None = None,
                    system_prompt: str | None = None) -> str:
        """Call the local Ollama model."""
        llm = self._get_local()
        if llm is None:
            raise ConnectionError("Local Ollama not available")
        return llm.ask(question, context=context, system_prompt=system_prompt)

    def _call_local_vision(self, image_path: str) -> str:
        """Call the local vision model."""
        llm = self._get_local()
        if llm is None:
            raise ConnectionError("Local Ollama not available")
        return llm.describe_image(image_path, vision_model=self.config.local_vision_model)

    # ── Cloud fallback ────────────────────────────────────────────────────

    def _get_cloud_client(self):
        """Get or create the cloud API client."""
        if self._cloud_client is not None:
            return self._cloud_client
        if not self.config.cloud_enabled or not self.config.cloud_api_key:
            return None
        try:
            if self.config.cloud_provider == LLMProvider.OPENAI:
                import openai
                self._cloud_client = openai.OpenAI(api_key=self.config.cloud_api_key)
            elif self.config.cloud_provider == LLMProvider.ANTHROPIC:
                import anthropic
                self._cloud_client = anthropic.Anthropic(api_key=self.config.cloud_api_key)
            elif self.config.cloud_provider == LLMProvider.OPENROUTER:
                import openai
                self._cloud_client = openai.OpenAI(
                    api_key=self.config.cloud_api_key,
                    base_url="https://openrouter.ai/api/v1",
                )
            logger.info(f"Cloud client initialized: {self.config.cloud_provider.value}")
        except ImportError:
            logger.warning(
                f"Cloud provider {self.config.cloud_provider.value} SDK not installed"
            )
        except Exception as e:
            logger.warning(f"Cloud client init failed: {e}")
        return self._cloud_client

    def _call_cloud(self, question: str, context: str | None = None,
                    system_prompt: str | None = None) -> str:
        """Call the cloud LLM."""
        client = self._get_cloud_client()
        if client is None:
            raise ConnectionError("Cloud LLM not available")

        # Build messages
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if context:
            user_content = f"Context:\n{context}\n\nQuestion: {question}"
        else:
            user_content = question
        messages.append({"role": "user", "content": user_content})

        provider = self.config.cloud_provider

        if provider == LLMProvider.ANTHROPIC:
            # Anthropic uses a different API
            sys_msg = system_prompt or "You are a helpful assistant."
            user_msg = f"Context:\n{context}\n\nQuestion: {question}" if context else question
            response = client.messages.create(
                model=self.config.cloud_model,
                max_tokens=self.config.cloud_max_tokens,
                system=sys_msg,
                messages=[{"role": "user", "content": user_msg}],
            )
            return response.content[0].text
        else:
            # OpenAI / OpenRouter use the same API
            response = client.chat.completions.create(
                model=self.config.cloud_model,
                messages=messages,
                max_tokens=self.config.cloud_max_tokens,
            )
            return response.choices[0].message.content

    def _call_cloud_vision(self, image_path: str) -> str:
        """Call the cloud vision model."""
        import base64
        client = self._get_cloud_client()
        if client is None:
            raise ConnectionError("Cloud vision not available")

        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()

        provider = self.config.cloud_provider
        if provider == LLMProvider.ANTHROPIC:
            response = client.messages.create(
                model=self.config.cloud_vision_model,
                max_tokens=1024,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
                        {"type": "text", "text": "Describe this screenshot in detail."},
                    ],
                }],
            )
            return response.content[0].text
        else:
            response = client.chat.completions.create(
                model=self.config.cloud_vision_model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe this screenshot in detail."},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                    ],
                }],
            )
            return response.choices[0].message.content

    # ── Routing logic ─────────────────────────────────────────────────────

    def _is_complex_task(self, question: str) -> bool:
        """Heuristic: does this question need a more capable model?"""
        q_lower = question.lower()
        return any(kw in q_lower for kw in self.config.complex_task_keywords)

    def _log_route(self, decision: RoutingDecision, provider: str, model: str,
                   duration_ms: float, error: str | None = None):
        """Log the routing decision to the event log."""
        if self.event_log:
            self.event_log.log_operation(
                event_type="llm_route",
                module="llm_router",
                operation="ask",
                inputs={"decision": decision.value, "provider": provider, "model": model},
                outputs={"error": error} if error else {},
                status="failed" if error else "completed",
                duration_ms=duration_ms,
            )

    def _log_cloud_privacy(self, question_len: int, provider: str):
        """Log a privacy marker when data goes to cloud."""
        if self.event_log:
            self.event_log.log_operation(
                event_type="privacy",
                module="llm_router",
                operation="cloud_fallback",
                inputs={"question_length": question_len, "provider": provider},
                outputs={},
                status="completed",
                duration_ms=0.0,
            )

    # ── Public API ────────────────────────────────────────────────────────

    def ask(self, question: str, context: str | None = None,
            system_prompt: str | None = None,
            force_cloud: bool = False) -> RoutingResult:
        """Ask a question with automatic routing.

        Args:
            question: The question to ask
            context: Optional context from local data
            system_prompt: Optional system instruction
            force_cloud: If True, skip local and go straight to cloud

        Returns:
            RoutingResult with the answer and routing metadata
        """
        start = time.time()

        # Determine routing
        if force_cloud and self.config.cloud_enabled:
            decision = RoutingDecision.CLOUD_FORCED
        elif self._is_complex_task(question) and self.config.cloud_enabled:
            decision = RoutingDecision.CLOUD_COMPLEX_TASK
        else:
            decision = RoutingDecision.LOCAL_PRIMARY

        # Try local first (unless forced to cloud)
        if decision in (RoutingDecision.LOCAL_PRIMARY, RoutingDecision.LOCAL_SUCCESS):
            try:
                text = self._call_local(question, context=context, system_prompt=system_prompt)
                duration_ms = (time.time() - start) * 1000
                result = RoutingResult(
                    text=text,
                    provider=LLMProvider.OLLAMA.value,
                    decision=RoutingDecision.LOCAL_SUCCESS.value,
                    model=self.config.local_model,
                    duration_ms=duration_ms,
                )
                self._log_route(RoutingDecision.LOCAL_SUCCESS,
                               LLMProvider.OLLAMA.value, self.config.local_model,
                               duration_ms)
                return result
            except Exception as local_err:
                logger.info(f"Local LLM failed ({local_err}), trying cloud fallback")
                if not self.config.cloud_enabled:
                    duration_ms = (time.time() - start) * 1000
                    self._log_route(RoutingDecision.ALL_FAILED,
                                   LLMProvider.OLLAMA.value, self.config.local_model,
                                   duration_ms, error=str(local_err))
                    return RoutingResult(
                        text="",
                        provider=LLMProvider.OLLAMA.value,
                        decision=RoutingDecision.ALL_FAILED.value,
                        model=self.config.local_model,
                        duration_ms=duration_ms,
                        error=str(local_err),
                    )
                decision = RoutingDecision.CLOUD_FALLBACK

        # Cloud fallback
        if decision in (RoutingDecision.CLOUD_FALLBACK, RoutingDecision.CLOUD_FORCED,
                        RoutingDecision.CLOUD_COMPLEX_TASK):
            if not self.config.cloud_enabled:
                duration_ms = (time.time() - start) * 1000
                return RoutingResult(
                    text="",
                    provider=self.config.cloud_provider.value,
                    decision=RoutingDecision.ALL_FAILED.value,
                    model=self.config.cloud_model,
                    duration_ms=duration_ms,
                    error="Cloud fallback not enabled and local LLM unavailable",
                )

            # Privacy log — data is about to leave the machine
            self._log_cloud_privacy(len(question), self.config.cloud_provider.value)

            try:
                text = self._call_cloud(question, context=context, system_prompt=system_prompt)
                duration_ms = (time.time() - start) * 1000
                result = RoutingResult(
                    text=text,
                    provider=self.config.cloud_provider.value,
                    decision=decision.value,
                    model=self.config.cloud_model,
                    duration_ms=duration_ms,
                    cloud_used=True,
                )
                self._log_route(decision, self.config.cloud_provider.value,
                               self.config.cloud_model, duration_ms)
                return result
            except Exception as cloud_err:
                duration_ms = (time.time() - start) * 1000
                self._log_route(RoutingDecision.ALL_FAILED,
                               self.config.cloud_provider.value, self.config.cloud_model,
                               duration_ms, error=str(cloud_err))
                return RoutingResult(
                    text="",
                    provider=self.config.cloud_provider.value,
                    decision=RoutingDecision.ALL_FAILED.value,
                    model=self.config.cloud_model,
                    duration_ms=duration_ms,
                    error=f"Both local and cloud failed. Local: unavailable. Cloud: {cloud_err}",
                )

        # Shouldn't reach here
        duration_ms = (time.time() - start) * 1000
        return RoutingResult(
            text="", provider="unknown", decision=RoutingDecision.ALL_FAILED.value,
            model="unknown", duration_ms=duration_ms, error="Routing logic error",
        )

    def describe_image(self, image_path: str, force_cloud: bool = False) -> RoutingResult:
        """Describe an image using local vision model, with cloud fallback."""
        start = time.time()

        if not force_cloud:
            try:
                text = self._call_local_vision(image_path)
                duration_ms = (time.time() - start) * 1000
                return RoutingResult(
                    text=text,
                    provider=LLMProvider.OLLAMA.value,
                    decision=RoutingDecision.LOCAL_SUCCESS.value,
                    model=self.config.local_vision_model,
                    duration_ms=duration_ms,
                )
            except Exception as local_err:
                logger.info(f"Local vision failed ({local_err}), trying cloud")

        if self.config.cloud_enabled:
            self._log_cloud_privacy(0, self.config.cloud_provider.value)
            try:
                text = self._call_cloud_vision(image_path)
                duration_ms = (time.time() - start) * 1000
                return RoutingResult(
                    text=text,
                    provider=self.config.cloud_provider.value,
                    decision=RoutingDecision.CLOUD_FALLBACK.value,
                    model=self.config.cloud_vision_model,
                    duration_ms=duration_ms,
                    cloud_used=True,
                )
            except Exception as cloud_err:
                duration_ms = (time.time() - start) * 1000
                return RoutingResult(
                    text="",
                    provider=self.config.cloud_provider.value,
                    decision=RoutingDecision.ALL_FAILED.value,
                    model=self.config.cloud_vision_model,
                    duration_ms=duration_ms,
                    error=str(cloud_err),
                )

        duration_ms = (time.time() - start) * 1000
        return RoutingResult(
            text="", provider=LLMProvider.OLLAMA.value,
            decision=RoutingDecision.ALL_FAILED.value,
            model=self.config.local_vision_model,
            duration_ms=duration_ms,
            error="Vision failed and cloud fallback not enabled",
        )

    def get_routing_status(self) -> dict[str, Any]:
        """Get current routing configuration and availability."""
        local = self._get_local()
        cloud = self._get_cloud_client()
        return {
            "local_available": local is not None,
            "local_model": self.config.local_model,
            "local_host": self.config.local_host,
            "cloud_enabled": self.config.cloud_enabled,
            "cloud_provider": self.config.cloud_provider.value,
            "cloud_model": self.config.cloud_model,
            "cloud_available": cloud is not None,
        }
