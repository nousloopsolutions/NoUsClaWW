"""Brain layer — community-side implementations of the BrainInterface socket.

This package provides concrete, local-first implementations of the
``sovereign_sockets.brain_interface.BrainInterface`` contract:

- :class:`LocalOllamaBrain` — wraps the existing :mod:`nousclaww.llm_interface`
  to satisfy the Brain socket using a local Ollama model.
- :class:`SocketLoader` — loads a Brain implementation from a dotted path
  or a local socket file, enabling the Nous MVP to plug in any brain that
  honours the contract.

All implementations are community-zone code: they import only from
``sovereign_sockets`` and the standard library. They never import from
``proprietary_core`` (enforced by the DAG check in ``check-dag``).
"""

from nousclaww.brain.local_ollama_brain import LocalOllamaBrain
from nousclaww.brain.socket_loader import SocketLoader

__all__ = ["LocalOllamaBrain", "SocketLoader"]
