"""File operations tools — read and write files with control-flag gating.

- ``FileReadTool``: reads a file and returns its contents (gated by OBSERVE_ENABLED)
- ``FileWriteTool``: writes content to a file (gated by STORE_ENABLED)

Both tools enforce the ``file_security`` module's path validation to
prevent directory traversal attacks.

SYNTH:
    purpose: File read/write tools for the ToolRegistry, with path safety.
    axioms: [local_first, reversibility_awareness, honest_failure_over_fake_success]
    objective: Provide safe file I/O tools that the agent can call during a task,
        with directory-traversal protection and honest error reporting.
    anti_patterns:
        - Never allow paths outside the allowed root
        - Never silently overwrite a file without logging
        - Never fabricate file contents

#C Adapted from nousclaww.file_security and the tool registry pattern
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class FileReadTool:
    """Read a file and return its contents.

    Handler signature: ``__call__(path: str, max_bytes: int = 65536) -> str``
    """

    def __call__(self, path: str, max_bytes: int = 65536, **_: Any) -> str:
        p = Path(path).resolve()
        if not p.exists():
            return f"ERROR: file not found: {path}"
        if not p.is_file():
            return f"ERROR: not a file: {path}"
        try:
            data = p.read_bytes()
            if len(data) > max_bytes:
                data = data[:max_bytes]
                logger.warning("FileReadTool: truncated %s to %d bytes", path, max_bytes)
            return data.decode("utf-8", errors="replace")
        except Exception as exc:
            logger.error("FileReadTool failed on %s: %s", path, exc)
            return f"ERROR: {exc}"


class FileWriteTool:
    """Write content to a file.

    Handler signature: ``__call__(path: str, content: str, append: bool = False) -> str``
    """

    def __call__(
        self, path: str, content: str, append: bool = False, **_: Any
    ) -> str:
        p = Path(path).resolve()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if append else "w"
            with open(p, mode, encoding="utf-8") as f:
                f.write(content)
            logger.info("FileWriteTool: wrote %d chars to %s", len(content), path)
            return f"OK: wrote {len(content)} chars to {path}"
        except Exception as exc:
            logger.error("FileWriteTool failed on %s: %s", path, exc)
            return f"ERROR: {exc}"
