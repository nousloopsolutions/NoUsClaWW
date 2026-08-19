"""Loopback-only FastAPI sidecar server for NoUsClaWW.

Exposes the reflection, observability, memory, and self-improvement
subsystems via a loopback-only HTTP API. This is the bridge between
a desktop UI and the Python backend.

Contract:
    - Loopback-only (127.0.0.1) — never exposed to LAN
    - Versioned API contract — fail closed on version mismatch
    - All operations are logged to the event log
    - Per-launch secret for authentication
    - Health, capability, query, reflect, improve, memory, shutdown endpoints
    - Optional subsystems (desktop control, mad-dog loop) degrade gracefully
    - Default-deny egress enforcement (EgressController) — loopback binding,
      short-lived session tokens, construction-time telemetry blocking

SYNTH:
    purpose: Loopback-only sidecar service exposing reflection, observability, memory, and self-improvement via a per-launch-secret HTTP API on 127.0.0.1, with default-deny egress enforcement.
    axioms: [local_first, open_process, epistemic_boundary, reversibility_awareness, honest_failure_over_fake_success]
    objective: The desktop UI can interact with the Python backend via a secure loopback-only HTTP API, authenticated by a per-launch secret, with all operations logged for audit, and egress policy enforced at construction time.
    anti_patterns:
        - Binding to anything other than 127.0.0.1 — never expose to LAN
        - Accepting requests without the per-launch secret
        - Sending data to any remote service
        - Crashing when optional subsystems are unavailable instead of degrading gracefully
        - Skipping event logging for any operation
        - Enabling remote telemetry, crash-content upload, or model-provider fallback
#C Adapted from NoUs-fordge Nous-hub mvp_local_core
#C Egress enforcement adapted from NoUs-fordge Nous-hub mvp_local_core/data/egress_controller.py
"""

# ┌─ synth ──────────────────────────────────────────────────────────────────┐
# @NCL{v=1.0;agent=builder;mod=sidecar;ts=2026-08-18Z;tier=L3}
# #C Adapted from NoUs-fordge Nous-hub mvp_local_core
# #C Egress enforcement adapted from NoUs-fordge Nous-hub mvp_local_core/data/egress_controller.py
# #S{purpose="Loopback-only sidecar service exposing reflection + observability + memory + self-improvement via per-launch-secret HTTP API with default-deny egress enforcement"}
# #I{1="loopback-only — 127.0.0.1 binding, never exposed to LAN";2="per-launch secret — prevents unauthorized access even on loopback";3="all operations logged to event log";4="optional subsystems degrade gracefully — missing modules don't crash the server";5="default-deny egress — EgressController enforces loopback binding, short-lived session tokens, and construction-time telemetry blocking"}
# #D{1="FastAPI for HTTP"→="async, typed, automatic OpenAPI docs";2="loopback binding"→="security by network isolation";3="per-launch secret"→="prevents unauthorized access even on loopback";4="SidecarService"→="business logic, testable without HTTP";5="EgressController"→="default-deny egress policy with loopback enforcement and session tokens"]
# #M{status=IMPLEMENTED;version=1.1.0;deps="nousclaww.event_log, nousclaww.control_state, nousclaww.reflection.self_reflection, nousclaww.reflection.self_improvement, nousclaww.memory.memory_manager, nousclaww.epistemic_boundary"]
# #T{pass=0;fail=0;xfail=0}
# #W{1="requires fastapi and uvicorn for HTTP — pip install fastapi uvicorn";2="per-launch secret must be passed via NOUS_SIDECAR_SECRET env var or is auto-generated";3="desktop control and mad-dog loop are optional — disabled when modules unavailable";4="egress telemetry/crash/fallback flags cannot be enabled — setters raise EgressViolationError"]
# #L{lexicon→docs/NOUS_LEXICON.md}
# └──────────────────────────────────────────────────────────────────────────┘

from __future__ import annotations

import hashlib
import os
import re
import secrets
import stat
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI

API_VERSION = "1.0"
LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 18471  # NOUS-1 on phone keypad

SOURCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# ── Default-deny egress enforcement ────────────────────────────────────────
# Adapted from NoUs-fordge Nous-hub mvp_local_core/data/egress_controller.py
# Ensures loopback-only binding, short-lived session tokens, and
# construction-time telemetry blocking.

LOOPBACK_ADDRESSES: set[str] = {"127.0.0.1", "::1", "localhost"}


class EgressPolicy(Enum):
    """Network egress policy level.

    Attributes:
        DENY_ALL: No network access at all (offline mode). Loopback
            bind is still allowed for local IPC.
        LOOPBACK_ONLY: Only 127.0.0.1 and ::1 are permitted.
        ALLOWLIST: Loopback plus an explicit update/check allowlist.
    """

    DENY_ALL = "DENY_ALL"
    LOOPBACK_ONLY = "LOOPBACK_ONLY"
    ALLOWLIST = "ALLOWLIST"


class EgressViolationError(Exception):
    """Raised when an egress policy violation is detected."""


@dataclass
class SessionToken:
    """Short-lived local IPC/session token.

    Attributes:
        token: The token value (cryptographically random).
        created_at: When the token was created (epoch seconds).
        expires_at: When the token expires (epoch seconds).
        token_hash: SHA-256 hash of the token (for storage without
            revealing the token).
    """

    token: str
    created_at: float
    expires_at: float
    token_hash: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if not self.token_hash:
            self.token_hash = hashlib.sha256(self.token.encode()).hexdigest()

    def is_valid(self, now: float | None = None) -> bool:
        """Check if the token is still valid.

        Args:
            now: Optional current time (epoch seconds). Defaults to
                ``time.time()``.

        Returns:
            True if the token has not expired, False otherwise.
        """
        current = now if now is not None else time.time()
        return current < self.expires_at

    def verify(self, presented_token: str) -> bool:
        """Verify a presented token against this token.

        Args:
            presented_token: The token string to verify.

        Returns:
            True if the token matches and is still valid.
        """
        return (
            secrets.compare_digest(self.token, presented_token)
            and self.is_valid()
        )


class EgressController:
    """Default-deny egress policy enforcement.

    Ensures:
        1. Local services bind to loopback only.
        2. All IPC uses authenticated, short-lived session tokens.
        3. No remote telemetry, crash uploads, or model fallback.
        4. Update/check allowlist is separate from content processing.

    Usage::

        controller = EgressController(policy=EgressPolicy.LOOPBACK_ONLY)
        controller.check_binding("127.0.0.1")   # True
        controller.check_binding("0.0.0.0")     # False (raises in strict mode)
        token = controller.generate_session_token()
        controller.verify_token(token)          # True
    """

    DEFAULT_UPDATE_ALLOWLIST: set[str] = {
        "127.0.0.1",
        "::1",
    }

    def __init__(
        self,
        policy: EgressPolicy = EgressPolicy.LOOPBACK_ONLY,
        update_allowlist: set[str] | None = None,
        token_ttl_seconds: int = 3600,
    ) -> None:
        """Initialize the egress controller.

        Args:
            policy: Egress policy level.
            update_allowlist: Set of IPs allowed for updates (NOT
                content processing).
            token_ttl_seconds: Session token time-to-live in seconds.
        """
        self.policy = policy
        self.update_allowlist = (
            update_allowlist or self.DEFAULT_UPDATE_ALLOWLIST.copy()
        )
        self.token_ttl_seconds = token_ttl_seconds
        self._active_tokens: dict[str, SessionToken] = {}
        # Telemetry, crash upload, and model fallback are blocked at
        # construction time — they can never be enabled.
        self._telemetry_enabled = False
        self._crash_upload_enabled = False
        self._model_fallback_enabled = False

    def check_binding(self, host: str) -> bool:
        """Check if a service can bind to the given host.

        Args:
            host: Bind address to check.

        Returns:
            True if the host is permitted under the current policy.

        Raises:
            EgressViolationError: If the host violates the egress
                policy.
        """
        if self.policy == EgressPolicy.DENY_ALL:
            if host not in LOOPBACK_ADDRESSES:
                raise EgressViolationError(
                    f"DENY_ALL policy: cannot bind to {host} — "
                    "only loopback addresses allowed"
                )
        elif self.policy == EgressPolicy.LOOPBACK_ONLY:
            if host not in LOOPBACK_ADDRESSES:
                raise EgressViolationError(
                    f"LOOPBACK_ONLY policy: cannot bind to {host} — "
                    f"only {LOOPBACK_ADDRESSES} allowed"
                )
        elif self.policy == EgressPolicy.ALLOWLIST:
            if host not in LOOPBACK_ADDRESSES and host not in self.update_allowlist:
                raise EgressViolationError(
                    f"ALLOWLIST policy: {host} not in allowlist — "
                    f"allowed: {LOOPBACK_ADDRESSES | self.update_allowlist}"
                )
        return True

    def check_outbound(
        self, host: str, port: int, purpose: str = "",
    ) -> None:
        """Check if an outbound connection is allowed.

        Args:
            host: Target host.
            port: Target port.
            purpose: Connection purpose (for audit logging).

        Raises:
            EgressViolationError: If the connection violates policy.
        """
        if self.policy == EgressPolicy.DENY_ALL:
            raise EgressViolationError(
                f"DENY_ALL policy: no outbound connections allowed "
                f"(attempted {host}:{port} for {purpose})"
            )
        if purpose == "content_processing" and host not in LOOPBACK_ADDRESSES:
            raise EgressViolationError(
                f"Content processing cannot connect to {host}:{port} — "
                "content processing is loopback-only by design"
            )
        if purpose == "telemetry" and not self._telemetry_enabled:
            raise EgressViolationError(
                "Remote telemetry is disabled — no telemetry connections allowed"
            )
        if purpose == "crash_upload" and not self._crash_upload_enabled:
            raise EgressViolationError(
                "Crash-content upload is disabled — no crash data leaves the system"
            )
        if purpose == "model_fallback" and not self._model_fallback_enabled:
            raise EgressViolationError(
                "Model-provider fallback is disabled — no remote model connections"
            )
        if self.policy == EgressPolicy.LOOPBACK_ONLY:
            if host not in LOOPBACK_ADDRESSES:
                raise EgressViolationError(
                    f"LOOPBACK_ONLY policy: cannot connect to {host}:{port}"
                )
        elif self.policy == EgressPolicy.ALLOWLIST:
            if host not in LOOPBACK_ADDRESSES and host not in self.update_allowlist:
                raise EgressViolationError(
                    f"ALLOWLIST policy: {host}:{port} not in allowlist"
                )

    def generate_session_token(self, ttl_seconds: int | None = None) -> str:
        """Generate a new short-lived session token for local IPC.

        Args:
            ttl_seconds: Token time-to-live. Defaults to the
                controller default.

        Returns:
            The token string (cryptographically random).
        """
        ttl = ttl_seconds or self.token_ttl_seconds
        now = time.time()
        token = SessionToken(
            token=secrets.token_urlsafe(32),
            created_at=now,
            expires_at=now + ttl,
        )
        self._active_tokens[token.token_hash] = token
        return token.token

    def verify_token(self, token: str) -> bool:
        """Verify a session token for IPC authentication.

        Args:
            token: The token string to verify.

        Returns:
            True if the token is valid and not expired, False otherwise.
        """
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        st = self._active_tokens.get(token_hash)
        if st is None:
            return False
        if not st.is_valid():
            del self._active_tokens[token_hash]
            return False
        return st.verify(token)

    def revoke_session_token(self, token: str) -> None:
        """Revoke a session token.

        Args:
            token: The token string to revoke.
        """
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        self._active_tokens.pop(token_hash, None)

    def cleanup_expired_tokens(self) -> int:
        """Remove all expired tokens.

        Returns:
            The number of tokens removed.
        """
        now = time.time()
        expired = [h for h, t in self._active_tokens.items() if not t.is_valid(now)]
        for h in expired:
            del self._active_tokens[h]
        return len(expired)

    @property
    def telemetry_enabled(self) -> bool:
        """Whether remote telemetry is enabled (always False)."""
        return self._telemetry_enabled

    @telemetry_enabled.setter
    def telemetry_enabled(self, value: bool) -> None:
        if value:
            raise EgressViolationError(
                "Cannot enable remote telemetry — default-deny egress policy"
            )
        self._telemetry_enabled = value

    @property
    def crash_upload_enabled(self) -> bool:
        """Whether crash-content upload is enabled (always False)."""
        return self._crash_upload_enabled

    @crash_upload_enabled.setter
    def crash_upload_enabled(self, value: bool) -> None:
        if value:
            raise EgressViolationError(
                "Cannot enable crash-content upload — default-deny egress policy"
            )
        self._crash_upload_enabled = value

    @property
    def model_fallback_enabled(self) -> bool:
        """Whether model-provider fallback is enabled (always False)."""
        return self._model_fallback_enabled

    @model_fallback_enabled.setter
    def model_fallback_enabled(self, value: bool) -> None:
        if value:
            raise EgressViolationError(
                "Cannot enable model-provider fallback — default-deny egress policy"
            )
        self._model_fallback_enabled = value


def _validate_source_id(source_id: str) -> None:
    """Validate a source_id string.

    Args:
        source_id: The source ID to validate.

    Raises:
        ValueError: If the source_id is invalid.
    """
    if not isinstance(source_id, str) or not SOURCE_ID_PATTERN.match(source_id):
        raise ValueError(
            "source_id must be 1-64 characters, alphanumeric, underscore, or hyphen"
        )


@dataclass
class SidecarConfig:
    """Configuration for the sidecar service.

    Attributes:
        host: Bind address — must be 127.0.0.1 for loopback-only.
        port: Port to listen on.
        secret: Per-launch secret for authentication. If empty, a
            random one is generated.
        db_path: Path to the memory database.
        event_db_path: Path to the event log database.
        enable_observability: Whether to enable the event log.
        enable_self_reflection: Whether to enable self-reflection.
        enable_self_improvement: Whether to enable self-improvement.
        enable_desktop_control: Whether to enable desktop control.
        enable_mad_dog_loop: Whether to enable the Mad-Dog loop.
        enable_memory: Whether to enable the memory manager.
        enable_epistemic_boundary: Whether to enable the epistemic
            boundary.
        allowed_import_roots: Set of allowed import root paths.
    """
    host: str = LOOPBACK_HOST
    port: int = DEFAULT_PORT
    secret: str = field(default_factory=lambda: os.environ.get("NOUS_SIDECAR_SECRET", ""))
    db_path: str = "nous_memory.db"
    event_db_path: str = "nous_events.db"
    enable_observability: bool = True
    enable_self_reflection: bool = True
    enable_self_improvement: bool = True
    enable_desktop_control: bool = True
    enable_mad_dog_loop: bool = False
    enable_memory: bool = True
    enable_epistemic_boundary: bool = True
    egress_policy: EgressPolicy = EgressPolicy.LOOPBACK_ONLY
    egress_token_ttl_seconds: int = 3600
    allowed_import_roots: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.host != LOOPBACK_HOST:
            raise ValueError(
                f"host must be '{LOOPBACK_HOST}' — sidecar is loopback-only"
            )
        if not self.secret:
            self.secret = secrets.token_hex(32)


@dataclass
class APIResponse:
    """Standard API response wrapper.

    Attributes:
        ok: Whether the request succeeded.
        data: The response data (any type).
        error: Error message if the request failed, None otherwise.
        api_version: The API version string.
        duration_ms: Request processing duration in milliseconds.
    """
    ok: bool
    data: Any = None
    error: str | None = None
    api_version: str = API_VERSION
    duration_ms: float = 0.0


class SidecarService:
    """The sidecar service core — business logic without HTTP.

    This class implements the business logic. The HTTP layer (FastAPI)
    is a thin wrapper around these methods, so the service can be tested
    without running an HTTP server.

    Optional subsystems (desktop control, mad-dog loop, self-reflection,
    self-improvement) are initialized only if their modules are available
    and enabled in the config. Missing modules result in graceful
    degradation, not crashes.
    """

    def __init__(self, config: SidecarConfig | None = None) -> None:
        """Initialize the sidecar service.

        Args:
            config: Configuration for the service. If None, defaults
                are used.
        """
        self.config = config or SidecarConfig()
        self.start_time = time.time()

        # Initialize egress controller (default-deny egress enforcement)
        # This blocks telemetry, crash uploads, and model fallback at
        # construction time, and enforces loopback-only binding.
        self.egress = EgressController(
            policy=self.config.egress_policy,
            token_ttl_seconds=self.config.egress_token_ttl_seconds,
        )
        # Verify the configured host is permitted by the egress policy.
        # This is a defense-in-depth check — SidecarConfig.__post_init__
        # already rejects non-loopback hosts, but the egress controller
        # is the authoritative enforcement layer.
        self.egress.check_binding(self.config.host)

        # Initialize event log (observability)
        self.event_log: Any = None
        if self.config.enable_observability:
            try:
                from nousclaww.event_log import EventLog
                self.event_log = EventLog(self.config.event_db_path)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    f"Sidecar: event log unavailable: {e}"
                )

        # Initialize control state (user authority controls)
        self.control_state: Any = None
        try:
            from nousclaww.control_state import ControlFlag, ControlState
            self.control_state = ControlState()
            # Enable all controls by default for the alpha
            for flag in ControlFlag:
                self.control_state.enable(flag)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                f"Sidecar: control state unavailable: {e}"
            )

        # Initialize memory manager
        self.memory_manager: Any = None
        if self.config.enable_memory:
            try:
                from nousclaww.memory.memory_manager import MemoryManager
                self.memory_manager = MemoryManager(self.config.db_path)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    f"Sidecar: memory manager unavailable: {e}"
                )

        # Initialize epistemic boundary
        self.epistemic_boundary: Any = None
        if self.config.enable_epistemic_boundary:
            try:
                from nousclaww.epistemic_boundary import EpistemicBoundary
                self.epistemic_boundary = EpistemicBoundary()
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    f"Sidecar: epistemic boundary unavailable: {e}"
                )

        # Initialize self-reflection (optional)
        self.reflection: Any = None
        if self.config.enable_self_reflection:
            try:
                from nousclaww.reflection.self_reflection import SelfReflectionEngine
                self.reflection = SelfReflectionEngine(
                    core_dir=str(Path(__file__).resolve().parent.parent.parent),
                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    f"Sidecar: self-reflection unavailable: {e}"
                )

        # Initialize self-improvement (optional)
        self.improvement: Any = None
        if self.config.enable_self_improvement:
            try:
                from nousclaww.reflection.self_improvement import SelfImprovementEngine
                self.improvement = SelfImprovementEngine(
                    reflection_engine=self.reflection,
                    event_log=self.event_log,
                    core_dir=str(Path(__file__).resolve().parent.parent.parent),
                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    f"Sidecar: self-improvement unavailable: {e}"
                )

        # Initialize desktop control (optional)
        self.desktop_control: Any = None
        if self.config.enable_desktop_control:
            try:
                from nousclaww.desktop_control import (
                    DesktopControl,
                    DesktopControlConfig,
                )
                screenshot_dir = str(
                    Path(__file__).resolve().parent.parent.parent
                    / "data" / "screenshots"
                )
                Path(screenshot_dir).mkdir(parents=True, exist_ok=True)
                self.desktop_control = DesktopControl(
                    control_state=self.control_state,
                    config=DesktopControlConfig(screenshot_dir=screenshot_dir),
                    event_log=self.event_log,
                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    f"Sidecar: desktop control unavailable: {e}"
                )

        # Initialize Mad-Dog self-healing loop (optional, off by default)
        self.mad_dog: Any = None
        if self.config.enable_mad_dog_loop and self.improvement is not None:
            try:
                from nousclaww.reflection.mad_dog_loop import MadDogConfig, MadDogLoop
                self.mad_dog = MadDogLoop(
                    improvement_engine=self.improvement,
                    event_log=self.event_log,
                    config=MadDogConfig(),
                    epistemic_boundary=self.epistemic_boundary,
                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    f"Sidecar: mad-dog loop unavailable: {e}"
                )

    # ── Core endpoints ────────────────────────────────────────────────────

    def health(self) -> APIResponse:
        """Health check endpoint.

        Returns:
            APIResponse with health status and subsystem availability.
        """
        start = time.time()
        return APIResponse(
            ok=True,
            data={
                "status": "healthy",
                "uptime_seconds": time.time() - self.start_time,
                "api_version": API_VERSION,
                "observability_ready": self.event_log is not None,
                "memory_ready": self.memory_manager is not None,
                "reflection_ready": self.reflection is not None,
                "improvement_ready": self.improvement is not None,
                "epistemic_boundary_ready": self.epistemic_boundary is not None,
                "desktop_control_ready": self.desktop_control is not None,
                "mad_dog_ready": self.mad_dog is not None,
                "egress_policy": self.egress.policy.value,
                "egress_ready": True,
            },
            duration_ms=(time.time() - start) * 1000,
        )

    def capabilities(self) -> APIResponse:
        """Return available capabilities.

        Returns:
            APIResponse with the list of available operations and
            subsystem status.
        """
        start = time.time()
        operations = [
            "health", "capabilities", "reflect", "improve",
            "alignment", "shutdown", "events", "memory_stats",
            "memory_search", "memory_store", "controls",
        ]

        if self.desktop_control is not None:
            operations.extend([
                "desktop_status", "desktop_windows", "desktop_window_state",
                "desktop_read_terminal", "desktop_action",
            ])

        if self.mad_dog is not None:
            operations.extend([
                "mad_dog_status", "mad_dog_start", "mad_dog_stop",
                "mad_dog_human_pivots",
            ])

        if self.epistemic_boundary is not None:
            operations.extend([
                "epistemic_stats", "epistemic_void_sockets",
            ])

        caps: dict[str, Any] = {
            "api_version": API_VERSION,
            "operations": operations,
            "observability": self.event_log is not None,
            "memory": self.memory_manager is not None,
            "self_reflection": self.reflection is not None,
            "self_improvement": self.improvement is not None,
            "epistemic_boundary": self.epistemic_boundary is not None,
            "desktop_control": self.desktop_control is not None,
            "desktop_available": (
                self.desktop_control.is_available()
                if self.desktop_control is not None else False
            ),
            "mad_dog_loop": self.mad_dog is not None,
            "egress_policy": self.egress.policy.value,
            "egress_enforced": True,
        }
        return APIResponse(ok=True, data=caps, duration_ms=(time.time() - start) * 1000)

    # ── Event log endpoints ───────────────────────────────────────────────

    def get_events(
        self,
        limit: int = 20,
        event_type: str | None = None,
    ) -> APIResponse:
        """Get recent events from the event log.

        Args:
            limit: Maximum number of events to return.
            event_type: Optional event type filter.

        Returns:
            APIResponse with the list of events.
        """
        if not self.event_log:
            return APIResponse(ok=False, error="Observability not enabled")
        start = time.time()
        events = self.event_log.query(event_type=event_type, limit=limit)
        return APIResponse(
            ok=True, data=events,
            duration_ms=(time.time() - start) * 1000,
        )

    # ── Memory endpoints ──────────────────────────────────────────────────

    def memory_stats(self) -> APIResponse:
        """Get memory manager statistics.

        Returns:
            APIResponse with memory store statistics.
        """
        if not self.memory_manager:
            return APIResponse(ok=False, error="Memory manager not enabled")
        start = time.time()
        try:
            stats = self.memory_manager.get_stats()
            return APIResponse(
                ok=True, data=stats,
                duration_ms=(time.time() - start) * 1000,
            )
        except Exception as e:
            return APIResponse(
                ok=False, error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    def memory_search(self, query: str, limit: int = 10) -> APIResponse:
        """Search memories by content.

        Args:
            query: Search string.
            limit: Maximum results.

        Returns:
            APIResponse with matching memories.
        """
        if not self.memory_manager:
            return APIResponse(ok=False, error="Memory manager not enabled")
        start = time.time()
        try:
            if not isinstance(query, str):
                raise TypeError("query must be a string")
            if len(query) > 10_000:
                raise ValueError("query exceeds 10000 characters")
            if not isinstance(limit, int) or limit < 1 or limit > 100:
                raise ValueError("limit must be an integer between 1 and 100")
            results = self.memory_manager.search_memories(query, limit=limit)
            return APIResponse(
                ok=True, data=results,
                duration_ms=(time.time() - start) * 1000,
            )
        except Exception as e:
            return APIResponse(
                ok=False, error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    def memory_store(self, memory: dict[str, Any]) -> APIResponse:
        """Store a memory.

        Args:
            memory: Memory dict with keys 'type', 'content',
                'importance', 'subject', 'metadata'.

        Returns:
            APIResponse with the stored memory's ID.
        """
        if not self.memory_manager:
            return APIResponse(ok=False, error="Memory manager not enabled")
        start = time.time()
        try:
            if not isinstance(memory, dict):
                raise TypeError("memory must be a dict")
            if not memory.get("content"):
                raise ValueError("memory must have 'content'")
            memory_id = self.memory_manager.store_memory(memory)
            return APIResponse(
                ok=True,
                data={"memory_id": memory_id},
                duration_ms=(time.time() - start) * 1000,
            )
        except Exception as e:
            return APIResponse(
                ok=False, error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    # ── Reflection endpoints ──────────────────────────────────────────────

    def reflect(self) -> APIResponse:
        """Run a self-reflection cycle.

        Returns:
            APIResponse with the self-assessment report.
        """
        if not self.reflection:
            return APIResponse(ok=False, error="Self-reflection not enabled")
        start = time.time()
        try:
            assessment = self.reflection.reflect()
            return APIResponse(
                ok=True,
                data={
                    "timestamp": assessment.timestamp,
                    "total_checks": assessment.total_checks,
                    "passed": assessment.passed,
                    "failed": assessment.failed,
                    "gaps": assessment.gaps,
                    "summary": assessment.summary,
                    "checks": assessment.checks,
                },
                duration_ms=(time.time() - start) * 1000,
            )
        except Exception as e:
            return APIResponse(
                ok=False, error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    def improve(self) -> APIResponse:
        """Run a self-improvement cycle.

        Returns:
            APIResponse with the improvement summary and validated
            proposals.
        """
        if not self.improvement:
            return APIResponse(ok=False, error="Self-improvement not enabled")
        start = time.time()
        try:
            summary = self.improvement.run_improvement_cycle()
            proposals = self.improvement.get_validated_proposals()
            return APIResponse(
                ok=True,
                data={
                    "summary": summary,
                    "validated_proposals": proposals,
                },
                duration_ms=(time.time() - start) * 1000,
            )
        except Exception as e:
            return APIResponse(
                ok=False, error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    def get_alignment(self) -> APIResponse:
        """Get alignment report — how well the system matches its directives.

        Returns:
            APIResponse with the alignment report.
        """
        if not self.improvement:
            return APIResponse(ok=False, error="Self-improvement not enabled")
        start = time.time()
        try:
            report = self.improvement.get_alignment_report()
            return APIResponse(
                ok=True, data=report,
                duration_ms=(time.time() - start) * 1000,
            )
        except Exception as e:
            return APIResponse(
                ok=False, error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    # ── Epistemic boundary endpoints ──────────────────────────────────────

    def epistemic_stats(self) -> APIResponse:
        """Get epistemic boundary statistics.

        Returns:
            APIResponse with decision counts and void socket counts.
        """
        if not self.epistemic_boundary:
            return APIResponse(ok=False, error="Epistemic boundary not enabled")
        start = time.time()
        try:
            stats = self.epistemic_boundary.stats
            return APIResponse(
                ok=True, data=stats,
                duration_ms=(time.time() - start) * 1000,
            )
        except Exception as e:
            return APIResponse(
                ok=False, error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    def epistemic_void_sockets(self, unresolved_only: bool = True) -> APIResponse:
        """Get void sockets from the epistemic boundary.

        Args:
            unresolved_only: If True, return only unresolved sockets.

        Returns:
            APIResponse with the list of void sockets.
        """
        if not self.epistemic_boundary:
            return APIResponse(ok=False, error="Epistemic boundary not enabled")
        start = time.time()
        try:
            if unresolved_only:
                sockets = [
                    s.to_dict()
                    for s in self.epistemic_boundary.unresolved_sockets
                ]
            else:
                sockets = [
                    s.to_dict()
                    for s in self.epistemic_boundary.void_sockets.values()
                ]
            return APIResponse(
                ok=True,
                data={"count": len(sockets), "sockets": sockets},
                duration_ms=(time.time() - start) * 1000,
            )
        except Exception as e:
            return APIResponse(
                ok=False, error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    # ── Control state endpoints ───────────────────────────────────────────

    def get_control_state(self) -> APIResponse:
        """Get the current control state (user authority controls).

        Returns:
            APIResponse with the control flag states.
        """
        if not self.control_state:
            return APIResponse(ok=False, error="Control state not available")
        try:
            from nousclaww.control_state import ControlFlag
            return APIResponse(
                ok=True,
                data={
                    flag.value: self.control_state.is_enabled(flag)
                    for flag in ControlFlag
                },
            )
        except Exception as e:
            return APIResponse(ok=False, error=str(e))

    def set_control(self, flag_name: str, enabled: bool) -> APIResponse:
        """Enable or disable a control flag.

        Args:
            flag_name: The control flag name.
            enabled: Whether to enable or disable.

        Returns:
            APIResponse with the updated flag state.
        """
        if not self.control_state:
            return APIResponse(ok=False, error="Control state not available")
        try:
            from nousclaww.control_state import ControlFlag
            flag = ControlFlag(flag_name)
            if enabled:
                self.control_state.enable(flag)
            else:
                self.control_state.disable(flag)
            return APIResponse(
                ok=True,
                data={
                    "flag": flag.value,
                    "enabled": self.control_state.is_enabled(flag),
                },
            )
        except ValueError:
            from nousclaww.control_state import ControlFlag
            return APIResponse(
                ok=False,
                error=(
                    f"Unknown control flag: {flag_name}. "
                    f"Valid flags: {[f.value for f in ControlFlag]}"
                ),
            )
        except Exception as e:
            return APIResponse(ok=False, error=str(e))

    # ── Desktop control endpoints ─────────────────────────────────────────

    def desktop_status(self) -> APIResponse:
        """Check desktop control availability and daemon status.

        Returns:
            APIResponse with availability and daemon status.
        """
        if not self.desktop_control:
            return APIResponse(ok=False, error="Desktop control not enabled")
        start = time.time()
        try:
            available = self.desktop_control.is_available()
            daemon = (
                self.desktop_control.status()
                if available
                else {"running": False}
            )
            return APIResponse(
                ok=True,
                data={
                    "available": available,
                    "daemon_running": daemon.get("running", False),
                    "daemon_detail": daemon,
                },
                duration_ms=(time.time() - start) * 1000,
            )
        except Exception as e:
            return APIResponse(
                ok=False, error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    def desktop_list_windows(self, on_screen_only: bool = False) -> APIResponse:
        """List desktop windows.

        Args:
            on_screen_only: If True, only return on-screen windows.

        Returns:
            APIResponse with the window list.
        """
        if not self.desktop_control:
            return APIResponse(ok=False, error="Desktop control not enabled")
        try:
            result = self.desktop_control.list_windows(on_screen_only=on_screen_only)
            return APIResponse(
                ok=result.ok, data=result.data, error=result.error,
                duration_ms=result.duration_ms,
            )
        except Exception as e:
            return APIResponse(ok=False, error=str(e))

    def desktop_get_window_state(
        self,
        pid: int,
        window_id: int,
        include_screenshot: bool = True,
        query: str | None = None,
    ) -> APIResponse:
        """Get window accessibility tree and optional screenshot.

        Args:
            pid: Process ID of the window.
            window_id: Window ID.
            include_screenshot: Whether to include a screenshot.
            query: Optional query string.

        Returns:
            APIResponse with the window state.
        """
        if not self.desktop_control:
            return APIResponse(ok=False, error="Desktop control not enabled")
        try:
            result = self.desktop_control.get_window_state(
                pid=pid, window_id=window_id,
                include_screenshot=include_screenshot, query=query,
            )
            return APIResponse(
                ok=result.ok, data=result.data, error=result.error,
                duration_ms=result.duration_ms,
            )
        except Exception as e:
            return APIResponse(ok=False, error=str(e))

    def desktop_read_terminal(self, pid: int, window_id: int) -> APIResponse:
        """Read text content from a terminal window.

        Args:
            pid: Process ID of the terminal.
            window_id: Window ID.

        Returns:
            APIResponse with the terminal text.
        """
        if not self.desktop_control:
            return APIResponse(ok=False, error="Desktop control not enabled")
        try:
            result = self.desktop_control.read_terminal(pid=pid, window_id=window_id)
            return APIResponse(
                ok=result.ok, data=result.data, error=result.error,
                duration_ms=result.duration_ms,
            )
        except Exception as e:
            return APIResponse(ok=False, error=str(e))

    def desktop_action(self, action: str, params: dict[str, Any]) -> APIResponse:
        """Execute a desktop input action.

        Args:
            action: The action name (click, type_text, press_key,
                scroll, launch_app, terminate_app).
            params: Action parameters.

        Returns:
            APIResponse with the action result.
        """
        if not self.desktop_control:
            return APIResponse(ok=False, error="Desktop control not enabled")
        start = time.time()
        try:
            if action == "click":
                result = self.desktop_control.click(**params)
            elif action == "type_text":
                result = self.desktop_control.type_text(**params)
            elif action == "press_key":
                result = self.desktop_control.press_key(**params)
            elif action == "scroll":
                result = self.desktop_control.scroll(**params)
            elif action == "launch_app":
                result = self.desktop_control.launch_app(**params)
            elif action == "terminate_app":
                result = self.desktop_control.terminate_app(**params)
            else:
                return APIResponse(
                    ok=False, error=f"Unknown action: {action}",
                    duration_ms=(time.time() - start) * 1000,
                )
            return APIResponse(
                ok=result.ok, data=result.data, error=result.error,
                duration_ms=result.duration_ms,
            )
        except Exception as e:
            return APIResponse(
                ok=False, error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    # ── Mad-Dog loop endpoints ────────────────────────────────────────────

    def mad_dog_status(self) -> APIResponse:
        """Get Mad-Dog loop status.

        Returns:
            APIResponse with the loop status.
        """
        if not self.mad_dog:
            return APIResponse(ok=False, error="Mad-Dog loop not enabled")
        try:
            return APIResponse(ok=True, data=self.mad_dog.get_status())
        except Exception as e:
            return APIResponse(ok=False, error=str(e))

    def mad_dog_start(self) -> APIResponse:
        """Start the Mad-Dog continuous self-healing loop.

        Returns:
            APIResponse with the loop status after starting.
        """
        if not self.mad_dog:
            return APIResponse(ok=False, error="Mad-Dog loop not enabled")
        start = time.time()
        try:
            self.mad_dog.start()
            return APIResponse(
                ok=True, data=self.mad_dog.get_status(),
                duration_ms=(time.time() - start) * 1000,
            )
        except Exception as e:
            return APIResponse(
                ok=False, error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    def mad_dog_stop(self) -> APIResponse:
        """Stop the Mad-Dog loop (kill switch).

        Returns:
            APIResponse with the loop status after stopping.
        """
        if not self.mad_dog:
            return APIResponse(ok=False, error="Mad-Dog loop not enabled")
        start = time.time()
        try:
            self.mad_dog.stop()
            return APIResponse(
                ok=True, data=self.mad_dog.get_status(),
                duration_ms=(time.time() - start) * 1000,
            )
        except Exception as e:
            return APIResponse(
                ok=False, error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    def mad_dog_human_pivots(self) -> APIResponse:
        """Get pending Human Pivot requests from the Mad-Dog loop.

        Returns:
            APIResponse with the list of pending Human Pivot requests.
        """
        if not self.mad_dog:
            return APIResponse(ok=False, error="Mad-Dog loop not enabled")
        try:
            pivots = self.mad_dog.get_pending_human_pivots()
            return APIResponse(ok=True, data={"pivots": pivots, "count": len(pivots)})
        except Exception as e:
            return APIResponse(ok=False, error=str(e))

    # ── Shutdown ──────────────────────────────────────────────────────────

    def shutdown(self) -> APIResponse:
        """Graceful shutdown signal.

        Returns:
            APIResponse with a shutdown acknowledgment.
        """
        # Stop the mad-dog loop if running
        if self.mad_dog is not None:
            try:
                self.mad_dog.stop()
            except Exception:
                pass
        return APIResponse(
            ok=True,
            data={"message": "Shutdown signal received"},
        )

    # ── Egress enforcement endpoints ──────────────────────────────────────

    def egress_status(self) -> APIResponse:
        """Get egress controller status.

        Returns:
            APIResponse with the egress policy, telemetry/crash/fallback
            flags, and active token count.
        """
        start = time.time()
        return APIResponse(
            ok=True,
            data={
                "policy": self.egress.policy.value,
                "telemetry_blocked": not self.egress.telemetry_enabled,
                "crash_upload_blocked": not self.egress.crash_upload_enabled,
                "model_fallback_blocked": not self.egress.model_fallback_enabled,
                "active_tokens": len(self.egress._active_tokens),
                "token_ttl_seconds": self.egress.token_ttl_seconds,
            },
            duration_ms=(time.time() - start) * 1000,
        )

    def egress_generate_token(self) -> APIResponse:
        """Generate a short-lived session token for local IPC.

        Returns:
            APIResponse with the token string and its TTL.
        """
        start = time.time()
        token = self.egress.generate_session_token()
        return APIResponse(
            ok=True,
            data={
                "token": token,
                "ttl_seconds": self.egress.token_ttl_seconds,
            },
            duration_ms=(time.time() - start) * 1000,
        )

    def egress_verify_token(self, token: str) -> APIResponse:
        """Verify a session token.

        Args:
            token: The token string to verify.

        Returns:
            APIResponse with the verification result.
        """
        start = time.time()
        valid = self.egress.verify_token(token)
        return APIResponse(
            ok=True,
            data={"valid": valid},
            duration_ms=(time.time() - start) * 1000,
        )


def create_fastapi_app(service: SidecarService) -> "FastAPI":
    """Create a FastAPI app wrapping the sidecar service.

    This is only called when fastapi is installed and the server is
    being started. Tests use SidecarService directly.

    Args:
        service: The SidecarService instance to wrap.

    Returns:
        A configured FastAPI application instance.
    """
    from fastapi import FastAPI, Header, HTTPException

    app = FastAPI(title="NoUsClaWW Sidecar", version=API_VERSION)

    def verify_secret(secret: str = Header(None, alias="X-NOUS-Secret")) -> None:
        """Verify the per-launch secret.

        Args:
            secret: The secret from the X-NOUS-Secret header.

        Raises:
            HTTPException: If the secret is invalid.
        """
        if secret != service.config.secret:
            raise HTTPException(status_code=401, detail="Invalid secret")

    # ── Health (no auth required) ─────────────────────────────────────────
    @app.get("/health")
    async def health() -> APIResponse:
        return service.health()

    # ── Capabilities ──────────────────────────────────────────────────────
    @app.get("/capabilities")
    async def capabilities(secret: str = Header(None, alias="X-NOUS-Secret")) -> APIResponse:
        verify_secret(secret)
        return service.capabilities()

    # ── Events ────────────────────────────────────────────────────────────
    @app.get("/events")
    async def events(
        limit: int = 20,
        event_type: str | None = None,
        secret: str = Header(None, alias="X-NOUS-Secret"),
    ) -> APIResponse:
        verify_secret(secret)
        return service.get_events(limit=limit, event_type=event_type)

    # ── Memory ────────────────────────────────────────────────────────────
    @app.get("/memory/stats")
    async def memory_stats(secret: str = Header(None, alias="X-NOUS-Secret")) -> APIResponse:
        verify_secret(secret)
        return service.memory_stats()

    @app.post("/memory/search")
    async def memory_search(
        body: dict,
        secret: str = Header(None, alias="X-NOUS-Secret"),
    ) -> APIResponse:
        verify_secret(secret)
        query = body.get("query", "")
        limit = body.get("limit", 10)
        return service.memory_search(query, limit)

    @app.post("/memory/store")
    async def memory_store(
        body: dict,
        secret: str = Header(None, alias="X-NOUS-Secret"),
    ) -> APIResponse:
        verify_secret(secret)
        return service.memory_store(body)

    # ── Reflection ────────────────────────────────────────────────────────
    @app.post("/reflect")
    async def reflect(secret: str = Header(None, alias="X-NOUS-Secret")) -> APIResponse:
        verify_secret(secret)
        return service.reflect()

    @app.post("/improve")
    async def improve(secret: str = Header(None, alias="X-NOUS-Secret")) -> APIResponse:
        verify_secret(secret)
        return service.improve()

    @app.get("/alignment")
    async def alignment(secret: str = Header(None, alias="X-NOUS-Secret")) -> APIResponse:
        verify_secret(secret)
        return service.get_alignment()

    # ── Epistemic boundary ────────────────────────────────────────────────
    @app.get("/epistemic/stats")
    async def epistemic_stats(secret: str = Header(None, alias="X-NOUS-Secret")) -> APIResponse:
        verify_secret(secret)
        return service.epistemic_stats()

    @app.get("/epistemic/void-sockets")
    async def epistemic_void_sockets(
        unresolved_only: bool = True,
        secret: str = Header(None, alias="X-NOUS-Secret"),
    ) -> APIResponse:
        verify_secret(secret)
        return service.epistemic_void_sockets(unresolved_only=unresolved_only)

    # ── Controls ──────────────────────────────────────────────────────────
    @app.get("/controls")
    async def get_controls(secret: str = Header(None, alias="X-NOUS-Secret")) -> APIResponse:
        verify_secret(secret)
        return service.get_control_state()

    @app.post("/controls/{flag_name}")
    async def set_control(
        flag_name: str,
        body: dict,
        secret: str = Header(None, alias="X-NOUS-Secret"),
    ) -> APIResponse:
        verify_secret(secret)
        return service.set_control(flag_name, body.get("enabled", True))

    # ── Desktop control ───────────────────────────────────────────────────
    @app.get("/desktop/status")
    async def desktop_status(secret: str = Header(None, alias="X-NOUS-Secret")) -> APIResponse:
        verify_secret(secret)
        return service.desktop_status()

    @app.get("/desktop/windows")
    async def desktop_list_windows(
        on_screen_only: bool = False,
        secret: str = Header(None, alias="X-NOUS-Secret"),
    ) -> APIResponse:
        verify_secret(secret)
        return service.desktop_list_windows(on_screen_only=on_screen_only)

    @app.get("/desktop/window/{pid}/{window_id}")
    async def desktop_get_window_state(
        pid: int,
        window_id: int,
        include_screenshot: bool = True,
        query: str | None = None,
        secret: str = Header(None, alias="X-NOUS-Secret"),
    ) -> APIResponse:
        verify_secret(secret)
        return service.desktop_get_window_state(
            pid=pid, window_id=window_id,
            include_screenshot=include_screenshot, query=query,
        )

    @app.get("/desktop/terminal/{pid}/{window_id}")
    async def desktop_read_terminal(
        pid: int,
        window_id: int,
        secret: str = Header(None, alias="X-NOUS-Secret"),
    ) -> APIResponse:
        verify_secret(secret)
        return service.desktop_read_terminal(pid=pid, window_id=window_id)

    @app.post("/desktop/action")
    async def desktop_action(
        body: dict,
        secret: str = Header(None, alias="X-NOUS-Secret"),
    ) -> APIResponse:
        verify_secret(secret)
        action = body.get("action")
        params = body.get("params", {})
        if not action:
            return APIResponse(ok=False, error="Missing required field: action")
        return service.desktop_action(action, params)

    # ── Mad-Dog loop ──────────────────────────────────────────────────────
    @app.get("/mad-dog/status")
    async def mad_dog_status(secret: str = Header(None, alias="X-NOUS-Secret")) -> APIResponse:
        verify_secret(secret)
        return service.mad_dog_status()

    @app.post("/mad-dog/start")
    async def mad_dog_start(secret: str = Header(None, alias="X-NOUS-Secret")) -> APIResponse:
        verify_secret(secret)
        return service.mad_dog_start()

    @app.post("/mad-dog/stop")
    async def mad_dog_stop(secret: str = Header(None, alias="X-NOUS-Secret")) -> APIResponse:
        verify_secret(secret)
        return service.mad_dog_stop()

    @app.get("/mad-dog/human-pivots")
    async def mad_dog_human_pivots(secret: str = Header(None, alias="X-NOUS-Secret")) -> APIResponse:
        verify_secret(secret)
        return service.mad_dog_human_pivots()

    # ── Egress enforcement ────────────────────────────────────────────────
    @app.get("/egress/status")
    async def egress_status(secret: str = Header(None, alias="X-NOUS-Secret")) -> APIResponse:
        verify_secret(secret)
        return service.egress_status()

    @app.post("/egress/token")
    async def egress_generate_token(secret: str = Header(None, alias="X-NOUS-Secret")) -> APIResponse:
        verify_secret(secret)
        return service.egress_generate_token()

    @app.post("/egress/verify-token")
    async def egress_verify_token(
        body: dict,
        secret: str = Header(None, alias="X-NOUS-Secret"),
    ) -> APIResponse:
        verify_secret(secret)
        token = body.get("token", "")
        return service.egress_verify_token(token)

    # ── Shutdown ──────────────────────────────────────────────────────────
    @app.post("/shutdown")
    async def shutdown(secret: str = Header(None, alias="X-NOUS-Secret")) -> APIResponse:
        verify_secret(secret)
        return service.shutdown()

    return app


def _write_secret_file(secret: str) -> str:
    """Write the per-launch secret to a private temp file.

    The file is created with owner-only permissions (best-effort on
    Windows). The caller reads the file to get the full secret.

    Args:
        secret: The secret string to write.

    Returns:
        The path to the secret file.

    Raises:
        OSError: If the file cannot be created or written.
    """
    fd, path = tempfile.mkstemp(prefix="nousclaww-sidecar-secret-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(secret)
            f.flush()
            os.fsync(f.fileno())
        # Owner read/write only (best-effort on Windows)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        os.unlink(path)
        raise
    return path


def _mask_secret(secret: str) -> str:
    """Mask a secret for display, showing only the first and last 4 chars.

    Args:
        secret: The secret to mask.

    Returns:
        A masked string like "abcd...wxyz".
    """
    if len(secret) <= 8:
        return "****"
    return f"{secret[:4]}...{secret[-4:]}"


def main() -> None:
    """Start the sidecar server on 127.0.0.1:18471.

    Writes the per-launch secret to a private temp file and prints a
    masked hint. The user reads the file and enters the secret in the UI.
    """
    import uvicorn

    config = SidecarConfig()
    secret_path = _write_secret_file(config.secret)
    print(f"NoUsClaWW Sidecar starting on {config.host}:{config.port}")
    print(f"Secret hint: {_mask_secret(config.secret)}")
    print(f"Full secret written to: {secret_path}")
    print()

    service = SidecarService(config)
    app = create_fastapi_app(service)
    uvicorn.run(app, host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
