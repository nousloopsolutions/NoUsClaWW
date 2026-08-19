// SYNTH:
//   purpose: Library catalog and first-match-wins categorization for the Rabbit Hole corpus.
//   axioms: [open_process, evidence_over_intuition, epistemic_boundary]
//   objective: Every file path maps to exactly one of 7 libraries deterministically; fuzzy queries resolve to a library ID.
//   anti_patterns:
//     - Ambiguous categorization (a file matching multiple libraries with equal priority)
//     - Hardcoding file paths instead of using keyword/extension rules
//     - Returning null from categorizeFile (must always return a valid library ID)
//     - Importing from proprietary_core

/**
 * VDS 60000 — Rabbit Hole library catalog + categorization rules.
 *
 * Single source of truth for the 7 libraries. Each corpus file maps to exactly
 * one library via first-match-wins keyword rules, with extension-based detection
 * for code/content and exact-filename detection for agent files.
 *
 * Adapted from the sovereign-context7 reference worker pattern.
 */

export interface Library {
  id: string;
  name: string;
  description: string;
  /** Keyword rules — first match wins. Checked against uppercased path. */
  keywords: string[];
}

export const LIBRARIES: Library[] = [
  {
    id: "nousclaw-governance",
    name: "Governance",
    description:
      "Sovereign governance, policy, axioms, security audits, standing orders, agent directives.",
    keywords: [
      "GOVERNANCE", "POLICY", "AXIOM", "SECURITY", "AUDIT", "MANIFESTO",
      "SOVEREIGN", "STANDING_ORDER", "VDS", "RETIREMENT",
      "MERGE_VERIFICATION", "ORGANIZATIONAL_SCHEMA", "SELF_CORRECTION",
      "SELF_REFLECTION", "CITATIONS", "DECISION", "GHOST_WRITER",
      "PHASE_GATE", "EXECUTION_SCOPE", "STRANGER_PERSONA",
      "AUTONOMOUS_PHASE", "AGENTS", "GOVERNANCE_STATUS",
      "CONTRIBUTING", "DECISION_LOG", "ADR",
    ],
  },
  {
    id: "nousclaw-intel",
    name: "Core Intel",
    description:
      "HRR math, Sherlock Protocol, curiosity engine, neural DNA index, HDC, pulse schema, Cheshire scrubber.",
    keywords: [
      "HRR", "SHERLOCK", "CURIOSITY", "ENGINE", "NEURAL", "DNA_INDEX",
      "GOLDEN_MEMORY", "GOLDEN_TESSERACT", "TESSERACT", "HDC",
      "PULSE_SCHEMA", "KPI", "SENSOR", "PULSE", "CHESHIRE", "SCRUBBER",
      "PIVOT", "COLOSSEUM", "NEURAL_PRUNING", "CONTEXT_CAPACITY",
      "NEXT_SESSION", "PHASE0", "DIMENSION_PROBE",
      "SENTIENT_HANDSHAKE", "MCP_OAUTH", "AIRLOCK", "SOVEREIGN_MEMORY",
      "KEY_PLACEMENT", "SPECIES_MEMORY", "HOLOGRAPHIC_MEMORY",
      "FALLACY", "INTEGRITY", "SCIENTIFIC_RIGOR",
    ],
  },
  {
    id: "nousclaw-architecture",
    name: "Architecture",
    description:
      "System architecture, deployment, integration roadmaps, Cloudflare structure, tunnels, desktop stack.",
    keywords: [
      "ARCHITECT", "DESKTOP", "TAURI", "ANDROID", "INSTALLATION",
      "INSTALLER", "DEPLOYMENT", "INTEGRATION", "TRIAD", "ROADMAP",
      "EXECUTION_MAP", "ENVIRONMENT_SETUP", "SETUP_NOUSCLAW", "LOCAL_DESKTOP",
      "BRIDGE", "RELAY", "API_NOUSLOOP",
      "CLOUDFLARE_SETUP", "CLOUDFLARE_TUNNEL",
      "DESKTOP_CLOUD", "DESKTOP_STACK", "DESKTOP_WAVE", "DESKTOP_GOLDEN",
      "DESKTOP_WORKSPACE", "ENGINE_FORGE", "SYSTEM_STATUS",
      "STUBS_AND_DEFERRED", "PHASE3", "PHASE2", "GAP_ANALYSIS",
      "IMPLEMENTATION_GUIDE", "INTEGRATED_TESTING", "REALITY_SYNC",
      "RUNBOOK", "IRON_CROWN", "PC_APP_LAUNCH", "BACKUP_AND_REDUNDANCY",
      "BACKUP_AND_COMMITS", "BACKUP_STATUS", "GIT_CRYPT", "GIT_LOCK",
      "MIGRATION_C_ANCHOR", "PATH_C_HARDWARE",
      "ANDROID_PORTABILITY", "ANDROID_RELAY", "APK_PULSE",
      "SOVEREIGN_CORE_INFRASTRUCTURE", "PHASE_II_BEHAVIORAL",
      "CURSOR_RULES", "DEV_MD_RETIREMENT", "NOUSCLAW_PROJECTS",
      "MASTER_TASK_LIST", "MVP_MASTER", "SURFACE_SCRUBBER",
      "DEPLOYMENT_SOVEREIGN", "INSTALLATION_READINESS", "INFANT_MORTALITY",
      "PARALLEL_SESSIONS", "NEXT_SESSIONS_TO_DISPATCH", "PIVOT_BACKLOG",
      "PIVOT_LEDGER", "RETIRED_FILES", "TELEMETRY_LOG", "FEATURES",
      "MANIFESTO_ROADMAP", "MANIFESTO_CHECKLIST", "REPOSITORY_DRIFT",
      "SECURITY_AUDIT", "MCP_OAUTH_OIDC", "CLOUDFLARE_ARCHITECTURE",
      "CLOUDFLARE_STATE", "CLOUDFLARE_CHANGE", "CLOUDFLARE_DECISIONS",
      "REFINERY", "WORKER.TS", "WRANGLER",
      "VDS_TOPOLOGY", "RABBIT_HOLE", "ELEVATOR_WIPE",
    ],
  },
  {
    id: "nousclaw-business",
    name: "Business",
    description:
      "Manifesto, business strategy, market, brand, revenue, funding, IP, legal, compliance, privacy, cognitive sovereignty.",
    keywords: [
      "MANIFESTO", "ROADMAP", "MASTER_TASK", "NOUSCLAW_PROJECTS",
      "ORGANIZATIONAL_SCHEMA", "STANDING_ORDER", "BUSINESS", "STRATEGY",
      "MARKET", "BRAND", "REVENUE", "FUNDING", "INVESTOR", "PARTNERSHIP",
      "MONETIZATION", "PRICING", "SUBSCRIPTION", "LICENSING", "IP",
      "INTELLECTUAL_PROPERTY", "PATENT", "TRADEMARK", "COPYRIGHT",
      "COMPLIANCE", "REGULATORY", "LEGAL", "TERMS", "PRIVACY",
      "DATA_RIGHTS", "COGNITIVE_SOVEREIGNTY", "BEHAVIOURAL_RIGHTS",
      "SAFETY_THROUGH_SEVERANCE", "PRIVACY_BY_SEVERANCE",
    ],
  },
  {
    id: "nousclaw-code",
    name: "Code",
    description:
      "Source code: TypeScript workers, Python services, Rust Tauri gateway. Function/class-level chunks.",
    keywords: [
      // Detected by extension in categorizeFile(), not keywords.
      // Keywords here are fallback for ambiguous paths.
      "SRC/", "REFINERY/", "SRC-TAURI/", "PYTHON/",
    ],
  },
  {
    id: "nousclaw-content",
    name: "Content",
    description:
      "Website content: HTML landing pages, icons, manifesto assets, rights-advocacy.",
    keywords: [
      "CONTENT/", "ASSETS/", "THE-ACT", "RIGHTS-ADVOCACY", "MANIFESTO/",
      "ICONS", ".HTML",
    ],
  },
  {
    id: "nousclaw-agent-files",
    name: "Agent Files",
    description:
      "Agent instruction files: AGENTS.md, CLAUDE.md, GEMINI.md, .mdc rules, SKILL.md, copilot-instructions.",
    keywords: [
      "AGENTS.MD", "CLAUDE.MD", "GEMINI.MD", ".MDC", "SKILL.MD",
      "COPILOT-INSTRUCTIONS", ".CURSOR/RULES", "JULES_FLEET_PROTOCOL",
      "JULES_AGENT_DIRECTIVES", "JULES_MERGE_TRACKER",
      "SAFETY_POLICY",
    ],
  },
];

export const LIBRARY_IDS = LIBRARIES.map((l) => l.id);

export const LIBRARY_BY_ID: Record<string, Library> = Object.fromEntries(
  LIBRARIES.map((l) => [l.id, l]),
);

/**
 * Categorize a file path into a library.
 * First-match-wins on keyword rules, with extension-based detection for code/content.
 * Agent files are detected by exact filename before keyword rules.
 *
 * @param relativePath The file path relative to the corpus root.
 * @returns A library ID (always returns a valid ID; defaults to architecture).
 */
export function categorizeFile(relativePath: string): string {
  const upper = relativePath.toUpperCase().replace(/\\/g, "/");

  // Extension-based detection (high priority for code/content)
  if (/\.(TS|TSX|JS|JSX|PY|RS|GO|JAVA|KT|SWIFT)$/.test(upper)) {
    // Agent rule files (.mdc) are handled below; code extensions -> nousclaw-code
    if (!upper.includes("AGENTS.MD") && !upper.includes(".MDC")) {
      return "nousclaw-code";
    }
  }
  if (/\.(HTML|HTM|CSS|SVG|WEBP|PNG|JPG|JPEG|GIF)$/.test(upper)) {
    return "nousclaw-content";
  }

  // Agent files (by exact filename)
  const basename = upper.split("/").pop() ?? upper;
  if (
    basename === "AGENTS.MD" ||
    basename === "CLAUDE.MD" ||
    basename === "GEMINI.MD" ||
    basename === "SKILL.MD" ||
    basename.endsWith(".MDC") ||
    basename === "COPILOT-INSTRUCTIONS.MD" ||
    upper.includes(".CURSOR/RULES/") ||
    upper.includes("JULES_FLEET_PROTOCOL") ||
    upper.includes("JULES_AGENT_DIRECTIVES") ||
    upper.includes("SAFETY_POLICY")
  ) {
    return "nousclaw-agent-files";
  }

  // Keyword rules — first match wins (skip extension-based libraries)
  for (const lib of LIBRARIES) {
    if (lib.id === "nousclaw-code" || lib.id === "nousclaw-content" || lib.id === "nousclaw-agent-files") {
      continue; // already handled above
    }
    for (const kw of lib.keywords) {
      if (upper.includes(kw.toUpperCase())) {
        return lib.id;
      }
    }
  }

  // Default: architecture (catch-all for technical docs)
  return "nousclaw-architecture";
}

/**
 * Fuzzy-resolve a library name query to a library ID.
 * Used by the resolve-library-id MCP tool.
 *
 * Resolution order:
 *   1. Direct ID match
 *   2. Library ID prefix match
 *   3. Library name match
 *   4. Keyword match
 *
 * @param query The user-provided library name or ID fragment.
 * @returns The resolved library ID, or null if no match.
 */
export function resolveLibraryId(query: string): string | null {
  const q = query.toLowerCase().trim();

  // Direct ID match
  if (LIBRARY_BY_ID[q]) return q;

  // Match on library id prefix
  for (const lib of LIBRARIES) {
    if (lib.id.toLowerCase().includes(q) || q.includes(lib.id.toLowerCase().replace("nousclaw-", ""))) {
      return lib.id;
    }
  }

  // Match on name
  for (const lib of LIBRARIES) {
    if (lib.name.toLowerCase().includes(q) || q.includes(lib.name.toLowerCase())) {
      return lib.id;
    }
  }

  // Keyword match
  const qUpper = q.toUpperCase();
  for (const lib of LIBRARIES) {
    for (const kw of lib.keywords) {
      if (kw.includes(qUpper) || qUpper.includes(kw)) {
        return lib.id;
      }
    }
  }

  return null;
}
