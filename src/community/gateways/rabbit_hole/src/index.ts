/**
 * VDS 60000 — Rabbit Hole Cloudflare Worker entry point.
 *
 * This is the only sanctioned gateway between the public internet (Surface) and the
 * NoUsClaWW MCP server (Floor). Every request must:
 *   1. Carry a valid Cloudflare Access JWT (CF-Access-Jwt-Assertion).
 *   2. Not belong to a revoked session (revoked:* in KV).
 *
 * On any failure the worker returns an EMPTY body (no information leak) with the
 * appropriate status code. Revoked sessions trigger ruthless session annihilation
 * via purgeSessionContext (deletes ALL 5 session key prefixes) and a 403.
 */
export interface Env {
  /** KV namespace holding all session state. */
  ELEVATOR_KV: KVNamespace;
  /** URL of the Red-Queen security sentry for audit logging. */
  SECURITY_SENTRY_URL: string;
  /** Cloudflare Access team domain used to fetch JWKS and verify issuer. */
  CF_ACCESS_TEAM_DOMAIN: string;
}

/** All session-related KV key prefixes that must be purged on annihilation. */
const SESSION_PREFIXES = ["session", "revoked", "tokens", "mcp_ctx", "bridge"];

/** JWKS cache (1-hour TTL). */
let jwksCache: { keys: Record<string, unknown>[]; fetchedAt: number } | null = null;
const JWKS_TTL_MS = 60 * 60 * 1000; // 1 hour

/**
 * Decode a base64url string into a Uint8Array.
 * Handles both base64url (- and _) and padding.
 */
export function base64UrlDecode(input: string): Uint8Array {
  const padLen = (4 - (input.length % 4)) % 4;
  const padded = input.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat(padLen);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

/** Decode a base64url JWT segment into a JSON object. */
function decodeJwtSegment(segment: string): Record<string, unknown> {
  const bytes = base64UrlDecode(segment);
  const json = new TextDecoder().decode(bytes);
  return JSON.parse(json) as Record<string, unknown>;
}

/**
 * Fetch the Cloudflare Access JWKS for the configured team domain.
 * Cached for 1 hour to avoid repeated network calls.
 */
export async function getJWKS(teamDomain: string): Promise<Record<string, unknown>[]> {
  const now = Date.now();
  if (jwksCache && now - jwksCache.fetchedAt < JWKS_TTL_MS) {
    return jwksCache.keys;
  }
  const url = `https://${teamDomain}/cdn-cgi/access/certs`;
  const resp = await fetch(url);
  if (!resp.ok) {
    throw new Error(`Failed to fetch JWKS: ${resp.status}`);
  }
  const data = (await resp.json()) as { keys: Record<string, unknown>[] };
  jwksCache = { keys: data.keys, fetchedAt: now };
  return data.keys;
}

/**
 * Verify a Cloudflare Access JWT signature using the Web Crypto API.
 *
 * Steps:
 *   1. Parse header + payload + signature.
 *   2. Validate iss claim matches the team domain.
 *   3. Validate exp is in the future.
 *   4. Find the matching key in JWKS by kid.
 *   5. Verify the signature with RS256 (RSA-SHA256).
 *
 * Returns the verified payload, or null if verification fails.
 */
export async function verifyCFAccessJWT(
  jwt: string,
  teamDomain: string
): Promise<Record<string, unknown> | null> {
  const parts = jwt.split(".");
  if (parts.length !== 3) {
    return null;
  }
  const [headerB64, payloadB64, signatureB64] = parts;

  let header: Record<string, unknown>;
  let payload: Record<string, unknown>;
  try {
    header = decodeJwtSegment(headerB64);
    payload = decodeJwtSegment(payloadB64);
  } catch {
    return null;
  }

  // Validate issuer.
  const expectedIss = `https://${teamDomain}`;
  if (payload["iss"] !== expectedIss) {
    return null;
  }

  // Validate expiry.
  const exp = payload["exp"];
  if (typeof exp !== "number") {
    return null;
  }
  const nowSeconds = Math.floor(Date.now() / 1000);
  if (exp <= nowSeconds) {
    return null;
  }

  // Validate audience presence (non-empty).
  if (!payload["aud"] || (Array.isArray(payload["aud"]) && payload["aud"].length === 0)) {
    return null;
  }

  // Find matching key by kid.
  const kid = header["kid"];
  if (typeof kid !== "string") {
    return null;
  }
  let keys: Record<string, unknown>[];
  try {
    keys = await getJWKS(teamDomain);
  } catch {
    return null;
  }
  const key = keys.find((k) => k["kid"] === kid);
  if (!key) {
    return null;
  }

  // Import the public key for signature verification.
  const algorithm = {
    name: "RSASSA-PKCS1-v1_5",
    hash: { name: "SHA-256" },
  };
  const jwk = {
    kty: key["kty"],
    n: key["n"],
    e: key["e"],
    kid: key["kid"],
    alg: key["alg"],
    ext: true,
  };
  let cryptoKey: CryptoKey;
  try {
    cryptoKey = await crypto.subtle.importKey("jwk", jwk, algorithm, false, ["verify"]);
  } catch {
    return null;
  }

  // Verify signature.
  const data = new TextEncoder().encode(`${headerB64}.${payloadB64}`);
  const signature = base64UrlDecode(signatureB64);
  const valid = await crypto.subtle.verify(
    "RSASSA-PKCS1-v1_5",
    cryptoKey,
    signature,
    data
  );
  if (!valid) {
    return null;
  }
  return payload;
}

/**
 * VDS 50000 ruthless session annihilation.
 *
 * Deletes ALL session-related KV keys across every prefix. No partial state
 * survives. Returns the list of deleted keys (for audit logging).
 */
export async function purgeSessionContext(
  sessionId: string,
  env: Env
): Promise<string[]> {
  const deleted: string[] = [];
  const listPromises: Promise<string[]>[] = SESSION_PREFIXES.map(async (prefix) => {
    const keys: string[] = [];
    let cursor: string | undefined;
    do {
      const list = await env.ELEVATOR_KV.list({ prefix: `${prefix}:${sessionId}`, cursor });
      for (const k of list.keys) {
        keys.push(k.name);
      }
      cursor = list.list_complete ? undefined : list.cursor;
    } while (cursor !== undefined);
    return keys;
  });
  const allKeys = (await Promise.all(listPromises)).flat();
  await Promise.all(
    allKeys.map(async (key) => {
      await env.ELEVATOR_KV.delete(key);
      deleted.push(key);
    })
  );
  return deleted;
}

/** Build a response with an empty body (no information leak). */
function emptyResponse(status: number): Response {
  return new Response(null, { status });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    // Only the MCP stream path is served; everything else is a 404 with empty body.
    if (url.pathname !== "/mcp/stream" && url.pathname !== "/") {
      return emptyResponse(404);
    }

    // 1. Require CF-Access-Jwt-Assertion.
    const jwt = request.headers.get("CF-Access-Jwt-Assertion");
    if (!jwt) {
      return emptyResponse(401);
    }

    // 2. Verify the JWT signature + claims.
    const payload = await verifyCFAccessJWT(jwt, env.CF_ACCESS_TEAM_DOMAIN);
    if (!payload) {
      return emptyResponse(401);
    }

    // 3. Derive session id from the sub claim (or aud). Fall back to a hash of email.
    const sessionId = (payload["sub"] as string) || (payload["email"] as string) || "unknown";
    if (!sessionId || sessionId === "unknown") {
      return emptyResponse(401);
    }

    // 4. Check for revoked session. If revoked, annihilate and 403.
    const revoked = await env.ELEVATOR_KV.get(`revoked:${sessionId}`);
    if (revoked) {
      await purgeSessionContext(sessionId, env);
      return emptyResponse(403);
    }

    // 5. Valid session — redirect to the MCP stream endpoint.
    if (url.pathname === "/") {
      return Response.redirect(`${url.origin}/mcp/stream`, 302);
    }

    // Already at /mcp/stream — pass through (the MCP server handles it).
    return new Response("MCP stream ready", { status: 200 });
  },
};
