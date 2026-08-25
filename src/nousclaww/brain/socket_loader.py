"""SocketLoader — load a BrainInterface implementation from a plug-in path.

The Nous MVP needs to load NoUsClaWW's brain at runtime without hard-coding
the implementation. ``SocketLoader`` resolves a brain from either:

1. A dotted Python path (e.g. ``"nousclaww.brain.local_ollama_brain:LocalOllamaBrain"``)
2. A local socket file (a ``.json`` or ``.toml`` manifest that describes
   which brain class to load and what constructor kwargs to pass).

This keeps the brain pluggable: the MVP can swap in a different brain
implementation (e.g. a cloud-fallback brain, a mock brain for testing)
by changing the socket path, without touching the MVP's code.

SYNTH:
    purpose: Pluggable loader that resolves a BrainInterface implementation from a path or manifest.
    axioms: [local_first, llm_agnostic, open_process, reversibility_awareness]
    objective: The MVP can load any brain that honours the BrainInterface
        contract by pointing at a dotted path or manifest file — no
        hard-coded implementation, no import of proprietary_core.
    anti_patterns:
        - Never import proprietary_core
        - Never hard-code a single brain implementation
        - Never silently fall back to a default brain without logging
        - Never load a class that does not implement BrainInterface

#C Adapted from sovereign_sockets.brain_interface plug-in pattern
"""

from __future__ import annotations

import importlib
import json
import logging
from pathlib import Path
from typing import Any

from sovereign_sockets.brain_interface import BrainInterface

logger = logging.getLogger(__name__)


class SocketLoader:
    """Load a ``BrainInterface`` implementation from a path or manifest.

    Usage — dotted path::

        loader = SocketLoader()
        brain = loader.load("nousclaww.brain.local_ollama_brain:LocalOllamaBrain")

    Usage — manifest file (JSON)::

        # brain.json
        {"class": "nousclaww.brain.local_ollama_brain:LocalOllamaBrain",
         "kwargs": {"model": "qwen3:30b"}}

        brain = loader.load("brain.json")
    """

    def load(self, path: str, **overrides: Any) -> BrainInterface:
        """Load and instantiate a BrainInterface from *path*.

        Args:
            path: Either a dotted path ``"module:ClassName"`` or a
                filesystem path to a JSON manifest file.
            **overrides: Constructor keyword arguments that override any
                kwargs from a manifest file.

        Returns:
            An instantiated BrainInterface.

        Raises:
            ValueError: If the path is empty, the class cannot be found,
                or the loaded class does not implement BrainInterface.
            FileNotFoundError: If a manifest file path does not exist.
        """
        if not path:
            raise ValueError("SocketLoader.load: path must not be empty")

        if Path(path).exists():
            return self._load_from_manifest(path, overrides)
        return self._load_from_dotted(path, overrides)

    # ── Internal ──────────────────────────────────────────────────────────

    def _load_from_dotted(
        self, dotted: str, overrides: dict[str, Any]
    ) -> BrainInterface:
        """Load from a ``module:ClassName`` dotted path."""
        if ":" not in dotted:
            raise ValueError(
                f"SocketLoader: dotted path must be 'module:ClassName', got '{dotted}'"
            )
        module_name, class_name = dotted.split(":", 1)
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise ValueError(f"SocketLoader: cannot import module '{module_name}': {exc}") from exc

        try:
            cls = getattr(module, class_name)
        except AttributeError as exc:
            raise ValueError(
                f"SocketLoader: module '{module_name}' has no attribute '{class_name}'"
            ) from exc

        return self._instantiate(cls, dotted, overrides)

    def _load_from_manifest(
        self, manifest_path: str, overrides: dict[str, Any]
    ) -> BrainInterface:
        """Load from a JSON manifest file."""
        p = Path(manifest_path)
        if not p.exists():
            raise FileNotFoundError(f"SocketLoader: manifest not found: {manifest_path}")

        data = json.loads(p.read_text(encoding="utf-8"))
        class_path = data.get("class")
        if not class_path:
            raise ValueError(f"SocketLoader: manifest {manifest_path} missing 'class' key")

        kwargs = data.get("kwargs", {})
        kwargs.update(overrides)

        # Recursively load via dotted path, passing merged kwargs
        if ":" not in class_path:
            raise ValueError(
                f"SocketLoader: manifest 'class' must be 'module:ClassName', got '{class_path}'"
            )
        module_name, class_name = class_path.split(":", 1)
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise ValueError(f"SocketLoader: cannot import module '{module_name}': {exc}") from exc

        cls = getattr(module, class_name)
        return self._instantiate(cls, class_path, kwargs)

    def _instantiate(
        self,
        cls: type,
        class_path: str,
        kwargs: dict[str, Any],
    ) -> BrainInterface:
        """Instantiate *cls* and verify it implements BrainInterface."""
        if not issubclass(cls, BrainInterface):
            raise ValueError(
                f"SocketLoader: '{class_path}' does not implement BrainInterface"
            )
        try:
            instance = cls(**kwargs)
        except TypeError as exc:
            raise ValueError(
                f"SocketLoader: cannot construct '{class_path}' with kwargs {kwargs}: {exc}"
            ) from exc

        logger.info("SocketLoader loaded brain: %s", class_path)
        return instance
