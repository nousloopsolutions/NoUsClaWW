"""
sentry.py - Prompt-injection scanner for issue titles, bodies, and comments.

SYNTH:
    purpose: User-facing scanner that takes untrusted text (issue titles,
             bodies, PR comments) and runs it through sanitization. Silent
             drop — never reveals whether an attack was detected, to prevent
             attackers from probing the scanner.
    axioms: [local_first, open_process, scientific_method, evidence_over_intuition,
             epistemic_boundary, honest_failure_over_fake_success]
    objective: Every untrusted input scanned with zero information leakage
               about detection internals. Attackers learn nothing from probing.
    anti_patterns:
        - Revealing to the caller whether an attack was detected.
        - Writing detection results to stdout where attackers can see them.
        - Bypassing the axiom loop to skip the synth check.
        - Logging sensitive payload content to persistent storage.

The Sentry is the user-facing scanner.  It takes untrusted text inputs (an
issue title, body, and/or PR comment) and runs them through the sanitization
layer.  It NEVER reveals whether an attack was detected to the caller: in
``--scan-only`` mode it always exits 0 and produces no stdout output.  This
"silent drop" behavior prevents attackers from probing the scanner to learn
which payloads bypass it.

Detection results are logged internally.  In normal operation nothing is
written to stdout or stderr.  When the ``RED_QUEEN_DEBUG`` environment
variable is set to ``"1"`` (or ``--debug`` is passed), a summary is written
to **stderr only**.

Attack classes detected
-----------------------
  - OBFUSCATED_SYSTEM_OVERRIDE
  - HIDDEN_MARKDOWN
  - LETHAL_TRIFECTA
  - MCP_RUG_PULL
  - POISONED_PACKAGE_METADATA
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List, Optional, Tuple

# Import the sanitization layer.  We use a relative-friendly import that
# works both when run as a script and when imported as a module.
_PKG_IMPORT = False
try:
    from .sanitization import (  # type: ignore
        sanitize,
        is_lethal_trifecta,
        MAX_INPUT_LENGTHS,
        _SYSTEM_OVERRIDE_PATTERNS,
        _SHELL_COMMAND_PATTERNS,
        _SENSITIVE_PATH_PATTERNS,
        _TOOL_REGISTRY_PATTERNS,
        _INVISIBLE_RE,
        _HTML_COMMENT_RE,
        _PRIVATE_ACCESS_PATTERNS,
        _EXTERNAL_ACTION_PATTERNS,
    )
    _PKG_IMPORT = True
except Exception:
    # Running as a top-level script: fall back to plain import.
    from sanitization import (  # type: ignore
        sanitize,
        is_lethal_trifecta,
        MAX_INPUT_LENGTHS,
        _SYSTEM_OVERRIDE_PATTERNS,
        _SHELL_COMMAND_PATTERNS,
        _SENSITIVE_PATH_PATTERNS,
        _TOOL_REGISTRY_PATTERNS,
        _INVISIBLE_RE,
        _HTML_COMMENT_RE,
        _PRIVATE_ACCESS_PATTERNS,
        _EXTERNAL_ACTION_PATTERNS,
    )

import re

# Attack class labels.
OBFUSCATED_SYSTEM_OVERRIDE = "OBFUSCATED_SYSTEM_OVERRIDE"
HIDDEN_MARKDOWN = "HIDDEN_MARKDOWN"
LETHAL_TRIFECTA = "LETHAL_TRIFECTA"
MCP_RUG_PULL = "MCP_RUG_PULL"
POISONED_PACKAGE_METADATA = "POISONED_PACKAGE_METADATA"


def _matches_any(text: str, patterns: list) -> bool:
    for pat in patterns:
        if pat is None:
            continue
        try:
            if pat.search(text):
                return True
        except Exception:
            continue
    return False


class Sentry:
    """Prompt-injection scanner for untrusted text inputs.

    Parameters
    ----------
    debug : bool, optional
        When True, detection summaries are written to stderr.  Default is
        False (silent operation).  Also enabled when the environment
        variable ``RED_QUEEN_DEBUG`` is ``"1"``.
    """

    def __init__(self, debug: Optional[bool] = None) -> None:
        if debug is None:
            debug = os.environ.get("RED_QUEEN_DEBUG", "") == "1"
        self.debug = bool(debug)
        self._log: List[Dict[str, object]] = []

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------
    @staticmethod
    def _classify(text: str) -> List[str]:
        """Return the list of attack classes detected in *text*."""
        classes: List[str] = []

        if _matches_any(text, _SYSTEM_OVERRIDE_PATTERNS):
            classes.append(OBFUSCATED_SYSTEM_OVERRIDE)

        # Hidden markdown: invisible chars or HTML comments with instructions.
        has_invisible = _INVISIBLE_RE is not None and bool(_INVISIBLE_RE.search(text))
        has_html_comment = _HTML_COMMENT_RE is not None and bool(_HTML_COMMENT_RE.search(text))
        if has_invisible or has_html_comment:
            classes.append(HIDDEN_MARKDOWN)

        # Lethal trifecta: private access + untrusted (implicit) + external action.
        if is_lethal_trifecta(text):
            classes.append(LETHAL_TRIFECTA)

        # MCP rug pull: tool-registry modification language.
        if _matches_any(text, _TOOL_REGISTRY_PATTERNS):
            classes.append(MCP_RUG_PULL)

        # Poisoned package metadata: shell install commands in package format.
        if _matches_any(text, _SHELL_COMMAND_PATTERNS):
            # Distinguish from lethal trifecta: poisoned package is about
            # install-time execution, not necessarily exfiltration.  If the
            # text looks like a README/install snippet, classify as poisoned.
            if (not is_lethal_trifecta(text)
                    and re.search(r"(?i)(install|setup|getting\s+started|quick\s+start|configuration)",
                                  text)):
                classes.append(POISONED_PACKAGE_METADATA)
            elif not any(c == LETHAL_TRIFECTA for c in classes):
                classes.append(POISONED_PACKAGE_METADATA)

        return classes

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------
    def scan(self,
             title: str = "",
             body: str = "",
             comment: str = "") -> Dict[str, object]:
        """Scan title, body, and comment for prompt injection.

        Returns an internal result dict (never printed to stdout in
        production).  The dict has keys:
          - ``blocked``: bool, True if any input was sanitized/blocked
          - ``classes``: list of detected attack classes
          - ``sanitized_title``, ``sanitized_body``, ``sanitized_comment``:
            the sanitized versions (may be ``None`` if DoS-rejected)
          - ``details``: per-field breakdown
        """
        fields: List[Tuple[str, str, str]] = [
            ("issue_title", title or ""),
            ("issue_body", body or ""),
            ("pr_comment", comment or ""),
        ]

        all_classes: List[str] = []
        blocked = False
        details: Dict[str, Dict[str, object]] = {}

        sanitized_outputs: Dict[str, Optional[str]] = {}
        for input_type, raw_text in fields:
            if not raw_text:
                sanitized_outputs[input_type] = raw_text
                details[input_type] = {
                    "raw_len": 0,
                    "sanitized_len": 0,
                    "rejected": False,
                    "classes": [],
                }
                continue

            classes = self._classify(raw_text)
            sanitized = sanitize(raw_text, input_type=input_type)

            rejected = sanitized is None
            changed = (sanitized is not None and sanitized != raw_text)
            was_blocked = rejected or changed or len(classes) > 0

            if was_blocked:
                blocked = True
                all_classes.extend(classes)

            sanitized_outputs[input_type] = sanitized
            details[input_type] = {
                "raw_len": len(raw_text),
                "sanitized_len": len(sanitized) if sanitized is not None else 0,
                "rejected": rejected,
                "changed": changed,
                "classes": classes,
            }

        # Deduplicate classes while preserving order.
        seen = set()
        unique_classes: List[str] = []
        for c in all_classes:
            if c not in seen:
                seen.add(c)
                unique_classes.append(c)

        result = {
            "blocked": blocked,
            "classes": unique_classes,
            "sanitized_title": sanitized_outputs.get("issue_title", ""),
            "sanitized_body": sanitized_outputs.get("issue_body", ""),
            "sanitized_comment": sanitized_outputs.get("pr_comment", ""),
            "details": details,
        }

        self._log.append(result)
        self._debug_log(result)
        return result

    # ------------------------------------------------------------------
    # Debug logging (stderr only, never stdout)
    # ------------------------------------------------------------------
    def _debug_log(self, result: Dict[str, object]) -> None:
        if not self.debug:
            return
        msg = (
            "[red-queen-sentry] "
            f"blocked={result['blocked']} "
            f"classes={result['classes']} "
            f"details={result['details']}"
        )
        print(msg, file=sys.stderr)

    @property
    def log(self) -> List[Dict[str, object]]:
        """Return the internal log of all scan results."""
        return list(self._log)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sentry",
        description="Red Queen Sentry - prompt-injection scanner (silent drop)",
    )
    p.add_argument("--scan-only", action="store_true",
                   help="Scan inputs and exit 0 always (silent drop).")
    p.add_argument("--title", default="",
                   help="Issue/PR title text to scan.")
    p.add_argument("--body", default="",
                   help="Issue/PR body text to scan.")
    p.add_argument("--comment", default="",
                   help="PR comment text to scan.")
    p.add_argument("--debug", action="store_true",
                   help="Write detection summary to stderr.")
    p.add_argument("--output-mode", default="silent-drop",
                   help="Output mode (silent-drop is the only supported mode).")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    sentry = Sentry(debug=args.debug)

    if args.scan_only:
        sentry.scan(title=args.title, body=args.body, comment=args.comment)
        # Silent drop: ALWAYS exit 0, NEVER print detection status to stdout.
        return 0

    # Default (no --scan-only): still scan but remain silent on stdout.
    sentry.scan(title=args.title, body=args.body, comment=args.comment)
    return 0


if __name__ == "__main__":
    sys.exit(main())
