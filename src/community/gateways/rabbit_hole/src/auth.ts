/**
 * VDS 60000 — MCP OAuth Gate.
 *
 * Validates bearer tokens presented to the Rabbit Hole worker. The initial
 * implementation performs structural validation and KV-backed session lookup.
 * It is upgradeable to a full OAuth 2.1 flow (RFC 9700) without changing the
 * public surface of `validateBearerToken`.
 *
 * Token-state fluctuation detection is delegated to elevator_wipe.ts; this
 * module only records the observed token state so fluctuation can be detected
 * on the next request.
 */
import type { Env } from "./index";

/** Shape of a stored token state record in KV (tokens:<sessionId>). */
export interface TokenState {
  /** The bearer token (or a hash thereof) currently bound to the session. */
  tokenHash: string;
  /** OAuth scopes granted to the token. */
  scopes: string[];
  /** Token issuer, for multi-tenant validation. */
  issuer: string;
  /** ISO-8601 expiry timestamp. */
  expiresAt: string;
  /** OAuth client id that minted the token. */
  clientId: string;
}

/** Result of bearer-token validation. */
export interface AuthResult {
  valid: boolean;
  sessionId?: string;
  tokenState?: TokenState;
  reason?: string;
}

/**
 * Hash a token using SHA-256 so we never store the raw bearer token in KV.
 * Returns a hex string.
 */
export async function hashToken(token: string): Promise<string> {
  const data = new TextEncoder().encode(token);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/**
 * Validate a bearer token from the Authorization header.
 *
 * Initial implementation:
 *   - Requires "Bearer <token>" scheme.
 *   - Hashes the token and looks up the stored token state in KV.
 *   - Rejects expired tokens.
 *
 * Upgrade path (OAuth 2.1):
 *   - Replace the KV lookup with an introspection call to the authorization server.
 *   - Validate scopes against the requested resource.
 *   - Enforce PKCE on all authorization-code flows.
 *
 * @param authHeader The raw Authorization header value.
 * @param env The worker environment (for KV access).
 */
export async function validateBearerToken(
  authHeader: string | null,
  env: Env
): Promise<AuthResult> {
  if (!authHeader) {
    return { valid: false, reason: "missing_authorization" };
  }
  const match = /^Bearer\s+(.+)$/i.exec(authHeader);
  if (!match) {
    return { valid: false, reason: "invalid_scheme" };
  }
  const token = match[1];
  if (!token || token.length < 8) {
    return { valid: false, reason: "malformed_token" };
  }

  const tokenHash = await hashToken(token);

  // Look up the session bound to this token hash. We store tokens:<sessionId>
  // with the tokenHash inside; to find the session we list the tokens: prefix.
  // In production this would be an introspection endpoint; the KV scan is the
  // initial gate.
  let cursor: string | undefined;
  do {
    const list = await env.ELEVATOR_KV.list({ prefix: "tokens:", cursor });
    for (const key of list.keys) {
      const raw = await env.ELEVATOR_KV.get(key.name);
      if (!raw) continue;
      let stored: TokenState;
      try {
        stored = JSON.parse(raw) as TokenState;
      } catch {
        continue;
      }
      if (stored.tokenHash !== tokenHash) continue;
      // Found the bound session. Check expiry.
      const expiresAtMs = Date.parse(stored.expiresAt);
      if (Number.isNaN(expiresAtMs) || expiresAtMs <= Date.now()) {
        return { valid: false, reason: "expired_token" };
      }
      const sessionId = key.name.slice("tokens:".length);
      return { valid: true, sessionId, tokenState: stored };
    }
    cursor = list.list_complete ? undefined : list.cursor;
  } while (cursor !== undefined);

  return { valid: false, reason: "unknown_token" };
}

/**
 * Record the observed token state for a session so fluctuation can be detected
 * on subsequent requests. Overwrites the previous record.
 */
export async function recordTokenState(
  sessionId: string,
  tokenState: TokenState,
  env: Env
): Promise<void> {
  await env.ELEVATOR_KV.put(`tokens:${sessionId}`, JSON.stringify(tokenState));
}

/**
 * OAuth 2.1 upgrade hook.
 *
 * When the authorization server is wired in, this function will perform remote
 * token introspection (RFC 7662) and return a normalized TokenState. Until then
 * it throws to make the upgrade boundary explicit.
 */
export async function introspectToken(
  _token: string,
  _introspectionEndpoint: string
): Promise<TokenState> {
  throw new Error(
    "OAuth 2.1 introspection is not yet configured. Wire the authorization server endpoint to enable."
  );
}
