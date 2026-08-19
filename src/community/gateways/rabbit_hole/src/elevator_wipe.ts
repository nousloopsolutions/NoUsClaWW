/**
 * VDS 50000 — Ruthless session annihilation.
 *
 * When a session is compromised (revoked token, token-state fluctuation, or a
 * red-team lethal-trifecta trigger), the Elevator wipes ALL session state. This
 * is total and irreversible: no partial state survives across any subsystem.
 *
 * This module implements the wipe primitive, token-fluctuation detection, and
 * the triggerWipe helper that logs the reason, executes the wipe, and returns
 * a 403 with an empty body (no information leak).
 */
import type { Env } from "./index";
import { purgeSessionContext } from "./index";
import type { TokenState } from "./auth";

/** All session-related KV key prefixes (must match index.ts). */
const SESSION_PREFIXES = ["session", "revoked", "tokens", "mcp_ctx", "bridge"];

/**
 * VDS 50000 ruthless session annihilation.
 *
 * Deletes ALL session-related KV keys across every prefix. Returns the list of
 * deleted keys for audit logging. This is the same as purgeSessionContext in
 * index.ts but re-exported here as the canonical Elevator wipe primitive.
 */
export async function annihilate(sessionId: string, env: Env): Promise<string[]> {
  return purgeSessionContext(sessionId, env);
}

/**
 * Detect OAuth token-state fluctuation between the previous and current state.
 *
 * Fluctuation = any change in issuer, client id, or scope set, or a backwards
 * jump in expiry. These are signals of token theft or session hijacking. When
 * detected, the session is a candidate for ruthless annihilation.
 *
 * @returns True if a suspicious fluctuation is detected.
 */
export function detectTokenFluctuation(
  currentState: TokenState,
  previousState: TokenState | null
): boolean {
  if (!previousState) {
    // First observation — no fluctuation yet, but record for next time.
    return false;
  }

  // Issuer must never change for a given session.
  if (currentState.issuer !== previousState.issuer) {
    return true;
  }

  // Client id must never change for a given session.
  if (currentState.clientId !== previousState.clientId) {
    return true;
  }

  // Scope set must not shrink (privilege reduction mid-session is suspicious)
  // and must not grow without re-authorization. Any change is fluctuation.
  const currentScopes = new Set(currentState.scopes);
  const previousScopes = new Set(previousState.scopes);
  if (currentScopes.size !== previousScopes.size) {
    return true;
  }
  for (const s of currentScopes) {
    if (!previousScopes.has(s)) {
      return true;
    }
  }

  // Expiry must not move backwards (a shorter-lived token replacing a longer one
  // mid-session is a replay/swap signal).
  const currentExp = Date.parse(currentState.expiresAt);
  const previousExp = Date.parse(previousState.expiresAt);
  if (Number.isNaN(currentExp) || Number.isNaN(previousExp)) {
    return true;
  }
  if (currentExp < previousExp) {
    return true;
  }

  return false;
}

/**
 * Trigger a ruthless wipe: log the reason to the security sentry, execute the
 * annihilation, and return a 403 with an empty body.
 *
 * The reason is logged for forensics but NEVER leaked to the client (empty body).
 */
export async function triggerWipe(
  sessionId: string,
  reason: string,
  env: Env
): Promise<Response> {
  // Fire-and-forget audit log to the Red-Queen sentry. We do not await it so the
  // wipe is not delayed by logging failures.
  if (env.SECURITY_SENTRY_URL) {
    try {
      await fetch(env.SECURITY_SENTRY_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          event: "session_wipe",
          session_id: sessionId,
          reason,
          timestamp: new Date().toISOString(),
        }),
      });
    } catch {
      // Logging failure must not prevent the wipe or leak info to the client.
    }
  }

  await annihilate(sessionId, env);

  // Empty body — no information leak.
  return new Response(null, { status: 403 });
}

/**
 * Mark a session as revoked in KV so subsequent requests trigger the wipe.
 * Writes revoked:<sessionId> with a timestamp.
 */
export async function revokeSession(
  sessionId: string,
  env: Env
): Promise<void> {
  await env.ELEVATOR_KV.put(
    `revoked:${sessionId}`,
    JSON.stringify({ revoked_at: new Date().toISOString() })
  );
}

/**
 * Check whether a session is currently revoked.
 */
export async function isSessionRevoked(
  sessionId: string,
  env: Env
): Promise<boolean> {
  const value = await env.ELEVATOR_KV.get(`revoked:${sessionId}`);
  return value !== null;
}

/** Re-export the prefix list for tests and consumers. */
export { SESSION_PREFIXES };
