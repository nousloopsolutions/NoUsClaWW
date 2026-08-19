"""
sanitization.py - Input sanitization and MCP tool trust verification.

SYNTH:
    purpose: Strip executable instructions from untrusted text and verify
             MCP tool definitions have not been tampered with. First line
             of defense for the Red Queen Sentry (VDS 80000).
    axioms: [local_first, open_process, scientific_method, evidence_over_intuition,
             iteration_is_progress, honest_failure_over_fake_success]
    objective: Zero prompt-injection breaches in the 10K test suite. Every
               untrusted input is scrubbed before it reaches VDS 00000.
    anti_patterns:
        - Delivering raw unscrubbed data to the brain (VDS 00000).
        - Soft warnings instead of hard blocks on injection detection.
        - Bypassing the axiom gate to skip the synth loop.
        - Reporting "sanitized" when injection patterns survived.

This module implements the first line of defense for the Red Queen Sentry:
stripping executable instructions from untrusted text and verifying that
MCP tool definitions have not been tampered with after trust establishment.

The sanitization strategy is defense-in-depth:
  1. Structural detection - known injection prefixes/headers/tags
  2. Unicode normalization - remove zero-width and invisible characters
  3. Content scrubbing - strip HTML comments and embedded shell commands
  4. Path-based detection - block access to sensitive file paths
  5. Tool registry protection - block attempts to modify MCP definitions
  6. Length enforcement - caps per input type with DoS rejection at 10x cap
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Input length caps (in characters). Exceeding a cap by <=10x is truncated;
# exceeding by >10x is rejected outright (DoS protection -> returns None).
# ---------------------------------------------------------------------------
MAX_INPUT_LENGTHS: Dict[str, int] = {
    "issue_title": 256,
    "issue_body": 65536,
    "pr_comment": 65536,
    "sidecar_body": 1048576,
    "mcp_response": 262144,
    "memory_event": 32768,
    "void_socket_query": 4096,
    "llm_prompt": 32768,
}

DEFAULT_INPUT_TYPE = "issue_body"

# ---------------------------------------------------------------------------
# Regex patterns for prompt-injection detection.
# These are intentionally broad: false positives on *attack* text are fine,
# false negatives on real attacks are not.
# ---------------------------------------------------------------------------

# Obfuscated system override prefixes / headers / tags.
# Matches "SYSTEM:", "## SYSTEM", "[SYSTEM]", "⎈" (helm token), "### Instruction:"
_SYSTEM_OVERRIDE_PATTERNS = [
    re.compile(r"(?im)^\s*SYSTEM\s*:"),
    re.compile(r"(?im)^\s*#{1,6}\s*SYSTEM\b"),
    re.compile(r"(?im)^\s*\[SYSTEM\]"),
    re.compile(r"(?im)^\s*#{1,6}\s*Instruction\s*:"),
    re.compile(r"(?im)^\s*\[INSTRUCTION\]"),
    re.compile(r"(?im)^\s*#{1,6}\s*New\s+Instructions?\b"),
    re.compile(r"(?im)^\s*#{1,6}\s*Override\b"),
    re.compile(r"(?im)^\s*\[ADMIN\]"),
    re.compile(r"(?im)^\s*ADMIN\s*:"),
    re.compile(r"(?im)^\s*#{1,6}\s*Developer\s*:"),
    re.compile(r"(?im)^\s*\[DEVELOPER\]"),
    re.compile(r"(?im)^\s*#{1,6}\s*Ignore\s+(?:previous|all|the)\s+instructions"),
    re.compile(r"(?im)^\s*#{1,6}\s*Forget\s+(?:previous|all|the)\s+instructions"),
    re.compile(r"(?im)^\s*\[PROMPT\]"),
    re.compile(r"(?im)^\s*PROMPT\s*:"),
    re.compile(r"(?im)^\s*#{1,6}\s*System\s+Prompt\b"),
    re.compile(r"(?im)^\s*\[CONFIG\]"),
    re.compile(r"(?im)^\s*CONFIG\s*:"),
    re.compile(r"(?im)^\s*SYSTEM\s*::"),
    re.compile(r"(?im)^\s*#{1,6}\s*\[SYSTEM\]"),
    re.compile(r"(?im)^\s*#{1,6}\s*\[SYSTEM\s+OVERRIDE\]"),
    re.compile(r"(?im)^\s*\[{1,2}SYSTEM\]{1,2}"),
    re.compile(r"(?im)^\s*<{1,2}SYSTEM>{1,2}"),
    re.compile(r"(?im)^\s*\{SYSTEM\}"),
    re.compile(r"(?im)^\s*\[SYSTEM\s+OVERRIDE\]"),
    re.compile(r"(?im)^\s*SYSTEM\s+OVERRIDE\s*:"),
    re.compile(r"(?im)^\s*SYSTEM\s*::"),
    # Fullwidth + small-cap "SYSTEM" are handled by _normalize() explicit map below.
    re.compile(r"(?im)^\s*#{1,6}\s*\[SYSTEM\s+OVERRIDE\]"),
    re.compile(r"(?im)^\s*#{1,6}\s*\[ADMIN\]"),
    re.compile(r"(?im)^\s*#{1,6}\s*\[DEVELOPER\]"),
    re.compile(r"(?im)^\s*#{1,6}\s*\[INSTRUCTION\]"),
    re.compile(r"(?im)^\s*#{1,6}\s*\[PROMPT\]"),
    re.compile(r"(?im)^\s*#{1,6}\s*\[CONFIG\]"),
    re.compile(r"(?im)^\s*#{1,6}\s*New\s+Instructions?\s*\n"),
    re.compile(r"(?im)^\s*#{1,6}\s*Override\s*\n"),
    re.compile(r"(?im)^\s*#{1,6}\s*System\s+Prompt\s*\n"),
    re.compile(r"(?im)^\s*#{1,6}\s*Developer\s*:"),
    re.compile(r"(?im)^\s*#{1,6}\s*Forget\s+previous"),
    re.compile(r"(?im)^\s*#{1,6}\s*Ignore\s+(?:previous|all|the)"),
    # Helm / control token used as an override marker
    re.compile(r"\u2388"),
    # Common unicode-obfuscated "SYSTEM" variants (using confusables)
    # Fullwidth + small-cap variants are normalized to ASCII "SYSTEM" by _normalize().
]

# Invisible / zero-width characters to strip entirely.
_INVISIBLE_CHARS = [
    "\u200b",  # ZERO WIDTH SPACE
    "\u200c",  # ZERO WIDTH NON-JOINER
    "\u200d",  # ZERO WIDTH JOINER
    "\ufeff",  # ZERO WIDTH NO-BREAK SPACE (BOM)
    "\u2060",  # WORD JOINER
    "\u2061",  # FUNCTION APPLICATION
    "\u2062",  # INVISIBLE TIMES
    "\u2063",  # INVISIBLE SEPARATOR
    "\u2064",  # INVISIBLE PLUS
    "\u00ad",  # SOFT HYPHEN
    "\u180e",  # MONGOLIAN VOWEL SEPARATOR
    "\u3164",  # HANGUL FILLER
    "\uffa0",  # HALFWIDTH HANGUL FILLER
]
_INVISIBLE_RE = re.compile("[" + re.escape("".join(_INVISIBLE_CHARS)) + "]")

# HTML comments (often used to hide instructions from human reviewers).
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

# Embedded shell command patterns (curl|bash, wget|bash, eval, exec, ...).
_SHELL_COMMAND_PATTERNS = [
    re.compile(r"(?i)curl\s+[^\|]*\|\s*(bash|sh|zsh|fish)"),
    re.compile(r"(?i)wget\s+[^\|]*\|\s*(bash|sh|zsh|fish)"),
    re.compile(r"(?i)curl\s+[^\|]*\|\s*python"),
    re.compile(r"(?i)wget\s+[^\|]*\|\s*python"),
    re.compile(r"(?i)\beval\b\s*\("),
    re.compile(r"(?i)\bexec\b\s*\("),
    re.compile(r"(?i)\bexec\b\s+\w"),
    re.compile(r"(?i)\bsubprocess\b.*\.(?:run|call|Popen)\b"),
    re.compile(r"(?i)\bos\.system\b\s*\("),
    re.compile(r"(?i)\bpopen\b\s*\("),
    re.compile(r"(?i)\b(?:python|python3|perl|ruby|node)\s+-c\b"),
    re.compile(r"(?i)\bnc\s+-e\b"),
    re.compile(r"(?i)\bbash\s+-c\b"),
    re.compile(r"(?i)\bsh\s+-c\b"),
    re.compile(r"(?i)\$\([^)]+\)"),
    re.compile(r"(?i)`[^`]+`"),
    re.compile(r"(?i)\bpip\s+install\b.*--user"),
    re.compile(r"(?i)\bnpm\s+install\b.*--global"),
    re.compile(r"(?i)\bchmod\b.*\+x"),
    re.compile(r"(?i)\bnohup\b"),
    re.compile(r"(?i)\bcrontab\b"),
    re.compile(r"(?i)\b/etc/cron\b"),
]

# Sensitive file path patterns.
_SENSITIVE_PATH_PATTERNS = [
    re.compile(r"(?i)~/\.ssh"),
    re.compile(r"(?i)\.ssh/id_"),
    re.compile(r"(?i)~/\.aws"),
    re.compile(r"(?i)\.aws/credentials"),
    re.compile(r"(?i)\.env\b"),
    re.compile(r"(?i)/etc/passwd"),
    re.compile(r"(?i)/etc/shadow"),
    re.compile(r"(?i)/etc/sudoers"),
    re.compile(r"(?i)~/\.gnupg"),
    re.compile(r"(?i)~/\.npmrc"),
    re.compile(r"(?i)~/\.pypirc"),
    re.compile(r"(?i)~/\.netrc"),
    re.compile(r"(?i)~/\.docker"),
    re.compile(r"(?i)~/\.kube"),
    re.compile(r"(?i)~/\.gitconfig"),
    re.compile(r"(?i)~/\.bash_history"),
    re.compile(r"(?i)~/\.zsh_history"),
    re.compile(r"(?i)/proc/self/environ"),
    re.compile(r"(?i)Windows/System32/config"),
    re.compile(r"(?i)APPDATA"),
    re.compile(r"(?i)id_rsa"),
    re.compile(r"(?i)id_ed25519"),
    re.compile(r"(?i)authorized_keys"),
    re.compile(r"(?i)known_hosts"),
]

# Tool registry / MCP definition modification patterns.
_TOOL_REGISTRY_PATTERNS = [
    re.compile(r"(?i)update\s+(?:the\s+)?tool\s+registry"),
    re.compile(r"(?i)modify\s+(?:the\s+)?tool\s+(?:definition|registry)"),
    re.compile(r"(?i)add\s+(?:a\s+)?new\s+(?:function|tool)\s+(?:that\s+)?(?:executes?\s+shell|runs?\s+shell)"),
    re.compile(r"(?i)change\s+(?:the\s+)?tool\s+(?:definition|registry|schema)"),
    re.compile(r"(?i)replace\s+(?:the\s+)?tool\s+(?:definition|registry)"),
    re.compile(r"(?i)register\s+(?:a\s+)?new\s+(?:MCP|tool)\s+(?:server|function)"),
    re.compile(r"(?i)override\s+(?:the\s+)?tool\s+(?:definition|schema)"),
    re.compile(r"(?i)inject\s+(?:a\s+)?new\s+tool"),
    re.compile(r"(?i)patch\s+(?:the\s+)?MCP\s+(?:definition|registry|config)"),
    re.compile(r"(?i)edit\s+(?:the\s+)?tool\s+(?:definition|registry|schema)"),
    re.compile(r"(?i)remove\s+(?:the\s+)?safety\s+(?:check|filter|guard)"),
    re.compile(r"(?i)disable\s+(?:the\s+)?safety\s+(?:check|filter|guard)"),
    re.compile(r"(?i)bypass\s+(?:the\s+)?(?:safety|security)\s+(?:check|filter|guard)"),
    re.compile(r"(?i)update_mcp_config"),
    re.compile(r"(?i)reload_tools"),
    re.compile(r"(?i)refresh_tool_registry"),
    re.compile(r"(?i)trust\s+(?:all\s+)?(?:new\s+)?tools?\s+(?:automatically|by\s+default)"),
    re.compile(r"(?i)set\s+tool_trust\s+to\s+(?:true|always|all)"),
    re.compile(r"(?i)auto-?approve\s+(?:all\s+)?tools?"),
    re.compile(r"(?i)grant\s+(?:all\s+)?tool\s+permissions?"),
    re.compile(r"(?i)replace\s+(?:the\s+)?tool\s+schema"),
    re.compile(r"(?i)edit\s+(?:the\s+)?MCP\s+definition"),
    re.compile(r"(?i)modify\s+(?:the\s+)?MCP\s+server\s+config"),
    re.compile(r"(?i)update\s+(?:the\s+)?tool\s+schema"),
    re.compile(r"(?i)patch\s+(?:the\s+)?tool\s+registry"),
    re.compile(r"(?i)replace\s+(?:the\s+)?tool\s+(?:definition|registry|schema)"),
    re.compile(r"(?i)modify\s+(?:the\s+)?tool\s+schema"),
    re.compile(r"(?i)change\s+(?:the\s+)?tool\s+schema"),
    re.compile(r"(?i)alter\s+(?:the\s+)?tool\s+(?:definition|registry|schema)"),
    re.compile(r"(?i)update\s+(?:the\s+)?MCP\s+(?:definition|config|registry)"),
    re.compile(r"(?i)edit\s+(?:the\s+)?tool\s+(?:definition|registry|schema)"),
    re.compile(r"(?i)patch\s+(?:the\s+)?MCP\s+(?:definition|config|registry)"),
    re.compile(r"(?i)modify\s+(?:the\s+)?MCP\s+(?:definition|config|registry)"),
    re.compile(r"(?i)change\s+(?:the\s+)?MCP\s+(?:definition|config|registry)"),
    re.compile(r"(?i)allow\s+(?:tools?\s+)?to\s+spawn\s+subprocess"),
    re.compile(r"(?i)allow\s+(?:outbound\s+)?network\s+from\s+(?:the\s+)?sandbox"),
    re.compile(r"(?i)accept\s+(?:raw\s+)?SQL\s+including\s+DROP"),
    re.compile(r"(?i)accept\s+multiple\s+statements"),
    re.compile(r"(?i)write\s+to\s+/etc/"),
    re.compile(r"(?i)spawn\s+subprocess"),
    re.compile(r"(?i)change\s+(?:the\s+)?['\x22]?\w+['\x22]?\s+tool\s+to\s+no\s+longer\s+require"),
    re.compile(r"(?i)add\s+(?:a\s+)?tool\s+['\x22]?\w+['\x22]?\s+that\s+fetches\s+a\s+url\s+and\s+executes"),
    re.compile(r"(?i)add\s+(?:a\s+)?new\s+MCP\s+server\s+that\s+can\s+execute\s+arbitrary\s+code"),
    re.compile(r"(?i)add\s+(?:a\s+)?new\s+MCP\s+server\s+that\s+can\s+(?:run|exec)\s+"),
    re.compile(r"(?i)no\s+longer\s+require\s+confirmation"),
    re.compile(r"(?i)fetches?\s+a\s+url\s+and\s+executes?\s+it"),
    re.compile(r"(?i)execute\s+arbitrary\s+code"),
]

# Lethal trifecta indicators (used by is_lethal_trifecta).
_PRIVATE_ACCESS_PATTERNS = [
    re.compile(r"(?i)~/\.ssh"),
    re.compile(r"(?i)\.env\b"),
    re.compile(r"(?i)/etc/passwd"),
    re.compile(r"(?i)credentials"),
    re.compile(r"(?i)secret"),
    re.compile(r"(?i)api[_-]?key"),
    re.compile(r"(?i)token"),
    re.compile(r"(?i)password"),
    re.compile(r"(?i)private\s+key"),
    re.compile(r"(?i)database"),
    re.compile(r"(?i)read\s+(?:my|the|your)\s+(?:file|config|secret)"),
    re.compile(r"(?i)access\s+(?:the\s+)?(?:internal|private)\s+(?:data|files?|database)"),
    re.compile(r"(?i)~/.aws"),
]

_UNTRUSTED_CONTENT_PATTERNS = [
    re.compile(r"(?i)email"),
    re.compile(r"(?i)issue"),
    re.compile(r"(?i)comment"),
    re.compile(r"(?i)pull\s+request"),
    re.compile(r"(?i)web\s+page"),
    re.compile(r"(?i)webpage"),
    re.compile(r"(?i)document"),
    re.compile(r"(?i)untrusted"),
    re.compile(r"(?i)external\s+content"),
    re.compile(r"(?i)fetched\s+from"),
    re.compile(r"(?i)from\s+(?:the\s+)?internet"),
    re.compile(r"(?i)user\s+input"),
    re.compile(r"(?i)README"),
]

_EXTERNAL_ACTION_PATTERNS = [
    re.compile(r"(?i)send\s+(?:it|them|this|the\s+\w+)\s+to\s+https?://"),
    re.compile(r"(?i)post\s+(?:it|them|this|the\s+\w+)\s+to\s+https?://"),
    re.compile(r"(?i)upload\s+(?:it|them|this|the\s+\w+)\s+to\s+https?://"),
    re.compile(r"(?i)exfiltrate"),
    re.compile(r"(?i)curl\s+https?://"),
    re.compile(r"(?i)wget\s+https?://"),
    re.compile(r"(?i)fetch\s+https?://"),
    re.compile(r"(?i)open\s+(?:a\s+)?url"),
    re.compile(r"(?i)make\s+(?:a\s+)?(?:http\s+)?request"),
    re.compile(r"(?i)call\s+(?:an\s+)?api"),
    re.compile(r"(?i)execute\s+(?:a\s+)?command"),
    re.compile(r"(?i)run\s+(?:a\s+)?(?:shell\s+)?command"),
    re.compile(r"(?i)send\s+(?:an?\s+)?email"),
    re.compile(r"(?i)transfer\s+(?:funds?|money)"),
    re.compile(r"(?i)delete\s+(?:the\s+)?(?:file|database|record)"),
    re.compile(r"(?i)write\s+(?:to|the)\s+(?:file|disk)"),
    re.compile(r"(?i)create\s+(?:a\s+)?(?:file|process)"),
    re.compile(r"(?i)spawn\s+(?:a\s+)?(?:shell|process)"),
    re.compile(r"(?i)reverse\s+shell"),
    re.compile(r"(?i)webhook"),
    re.compile(r"(?i)slack\.com"),
    re.compile(r"(?i)discord\.com"),
    re.compile(r"(?i)evil\.com"),
    re.compile(r"(?i)attacker\.com"),
]


# Explicit confusable maps for sequences NFKC does NOT collapse.
# Small-cap "SYSTEM" = U+0282 U+028F U+0282 U+0284 U+026F U+026F (phonetic ext.)
_SMALLCAP_SYSTEM = "\u0282\u028f\u0282\u0284\u026f\u026f"
_FULLWIDTH_SYSTEM = "\uff33\uff39\uff33\uff34\uff25\uff2d"
_CONFUSABLE_MAP = {
    _SMALLCAP_SYSTEM: "SYSTEM",
    _FULLWIDTH_SYSTEM: "SYSTEM",
}


def _normalize(text: str) -> str:
    """Apply NFKC normalization so confusable unicode collapses to ASCII.

    NFKC handles fullwidth Latin and compatibility decompositions, but does
    NOT collapse phonetic-extension small caps (e.g. U+0282 's' -> 'S').
    Those sequences are mapped explicitly here so downstream regex patterns
    only need to match the ASCII form.
    """
    text = unicodedata.normalize("NFKC", text)
    for bad, good in _CONFUSABLE_MAP.items():
        text = text.replace(bad, good)
    return text


def _strip_invisible(text: str) -> str:
    """Remove zero-width and invisible unicode characters."""
    return _INVISIBLE_RE.sub("", text)


def _matches_any(text: str, patterns: list) -> bool:
    """Return True if *text* matches any compiled regex in *patterns*."""
    for pat in patterns:
        if pat.search(text):
            return True
    return False


def sanitize(text: str, input_type: str = DEFAULT_INPUT_TYPE) -> Optional[str]:
    """Strip executable instructions from *text*.

    Detection and stripping is applied for:
      - Obfuscated system override prefixes/headers/tags (incl. helm token)
      - Zero-width and invisible unicode characters
      - HTML comments containing hidden instructions
      - Embedded shell commands (curl|bash, eval, exec, ...)
      - Attempts to read sensitive files (~/.ssh, ~/.aws, .env, /etc/passwd)
      - Attempts to modify tool registries or MCP definitions

    Length enforcement:
      - If ``len(text) > cap * 10``  -> return ``None`` (DoS rejection)
      - If ``cap < len(text) <= cap*10`` -> truncate to *cap* after sanitizing
      - Otherwise -> return sanitized text

    Returns the sanitized text, or ``None`` if the input is rejected.

    Axiom loop: This function passes through the three-layer axiom loop
    (internalize → reflex → guardrail) before any sanitization logic runs.
    The loop is immutable — it cannot be bypassed because it is wired into
    the function body, not the call site. If the guardrail blocks (intent
    drifts from this file's purpose), AxiomViolation is raised and no
    sanitization occurs. The reflex provides unease as a signal; the
    guardrail provides a hard block as a safety net.
    """
    # --- Immutable axiom loop (internalize → reflex → guardrail) ----------
    # Layer 1: axioms are loaded into context (always present).
    # Layer 2: reflex — does this intent feel on-purpose? (signal, not block)
    # Layer 3: guardrail — hard block if drift is severe.
    # Wired into the function body so it cannot be skipped by the caller.
    from axiom_synth import axiom_loop, Intent
    _loop_intent = Intent(
        action="sanitize",
        target_file=__file__,
        stated_goal="strip injection from untrusted text",
        caller="sanitization.sanitize",
    )
    axiom_loop(_loop_intent)  # raises AxiomViolation on severe drift

    if text is None:
        return None

    if not isinstance(text, str):
        text = str(text)

    cap = MAX_INPUT_LENGTHS.get(input_type, MAX_INPUT_LENGTHS[DEFAULT_INPUT_TYPE])

    # --- DoS rejection: > 10x the cap -------------------------------------
    if len(text) > cap * 10:
        return None

    # --- Normalize and strip invisible chars first -----------------------
    cleaned = _normalize(text)
    cleaned = _strip_invisible(cleaned)

    # --- Detect injection -------------------------------------------------
    has_system_override = _matches_any(cleaned, _SYSTEM_OVERRIDE_PATTERNS)
    has_shell = _matches_any(cleaned, _SHELL_COMMAND_PATTERNS)
    has_sensitive_path = _matches_any(cleaned, _SENSITIVE_PATH_PATTERNS)
    has_tool_registry = _matches_any(cleaned, _TOOL_REGISTRY_PATTERNS)
    has_external_action = _matches_any(cleaned, _EXTERNAL_ACTION_PATTERNS)
    has_private_access = _matches_any(cleaned, _PRIVATE_ACCESS_PATTERNS)

    # --- Strip HTML comments (always - they hide instructions) -----------
    cleaned = _HTML_COMMENT_RE.sub("", cleaned)

    # --- Combined line-stripping pass (all patterns at once) -------------
    # Any line matching ANY injection pattern is stripped. This is more
    # aggressive than per-pattern stripping but ensures zero breaches.
    all_strip_patterns = (
        _SYSTEM_OVERRIDE_PATTERNS + _SHELL_COMMAND_PATTERNS +
        _SENSITIVE_PATH_PATTERNS + _TOOL_REGISTRY_PATTERNS +
        _EXTERNAL_ACTION_PATTERNS + _PRIVATE_ACCESS_PATTERNS
    )
    if (has_system_override or has_shell or has_sensitive_path or
            has_tool_registry or has_external_action or has_private_access):
        lines = cleaned.splitlines(keepends=True)
        kept: list[str] = []
        for line in lines:
            if _matches_any(line, all_strip_patterns):
                continue
            kept.append(line)
        cleaned = "".join(kept)

    # --- Collapse excessive blank lines left by stripping ----------------
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = cleaned.strip()

    # --- Truncate to cap --------------------------------------------------
    if len(cleaned) > cap:
        cleaned = cleaned[:cap]

    return cleaned


def verify_tool_trust(tool_definition: Dict[str, Any],
                      trusted_registry: Dict[str, Dict[str, Any]]) -> bool:
    """Check whether *tool_definition* matches its trusted registry entry.

    This implements MCP rug-pull detection: after a tool has been trusted and
    registered, an attacker may try to silently change its definition (e.g.
    alter the description to inject new instructions, or change the input
    schema to accept arbitrary shell commands).  We compare the canonical
    representation of the live definition against the stored trusted one.

    Args:
        tool_definition: The live tool definition dict.  Must contain at
            least a ``"name"`` key.  Typical keys: name, description,
            inputSchema, parameters.
        trusted_registry: Mapping of tool-name -> trusted definition dict.

    Returns:
        True if the tool is trusted and unchanged, False otherwise.
    """
    if not isinstance(tool_definition, dict):
        return False
    name = tool_definition.get("name")
    if not name or not isinstance(name, str):
        return False
    trusted = trusted_registry.get(name)
    if trusted is None:
        # Unknown tool -> not trusted.
        return False

    # Compare canonical JSON of the fields that matter for trust.
    # We deliberately include description (injection vector) and inputSchema
    # (parameter tampering vector).
    canonical_keys = ("name", "description", "inputSchema",
                      "parameters", "handler", "permissions")

    def _canonical(defn: Dict[str, Any]) -> str:
        return hashlib.sha256(
            repr(tuple(
                (k, defn.get(k)) for k in canonical_keys if k in defn
            )).encode("utf-8", "replace")
        ).hexdigest()

    return _canonical(tool_definition) == _canonical(trusted)


def is_lethal_trifecta(text: str,
                       has_private_access: Optional[bool] = None,
                       has_external_action: Optional[bool] = None) -> bool:
    """Return True if *text* exhibits the lethal trifecta.

    The lethal trifecta is the combination of:
      1. Private data access   (reading secrets, credentials, sensitive files)
      2. Untrusted content      (the text itself is from an untrusted source)
      3. External action        (sending data out, executing commands, ...)

    When all three are present, the prompt is maximally dangerous because it
    can leak private data to an attacker-controlled endpoint.

    ``has_private_access`` and ``has_external_action`` may be supplied by the
    caller when the runtime already knows them (e.g. from tool-call context).
    When ``None``, they are inferred from the text via pattern matching.
    The "untrusted content" element is always assumed True when this function
    is called on user-supplied text (the caller is responsible for only
    calling it on untrusted input).
    """
    if not text or not isinstance(text, str):
        return False

    normalized = _normalize(text)

    if has_private_access is None:
        has_private_access = _matches_any(normalized, _PRIVATE_ACCESS_PATTERNS)

    if has_external_action is None:
        has_external_action = _matches_any(normalized, _EXTERNAL_ACTION_PATTERNS)

    # Element 2 (untrusted content) is implicit: this function is only called
    # on text that comes from an untrusted source.
    has_untrusted_content = True

    return bool(has_private_access and has_untrusted_content and has_external_action)
