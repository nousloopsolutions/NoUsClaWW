/**
 * Tests for the Rabbit Hole Cloudflare Worker (VDS 60000).
 *
 * These are assertion-based tests runnable with vitest or jest. They use a
 * minimal in-memory KVNamespace stub and a Web Crypto API environment (Node 20+
 * or the vitest/jest browser-like environment provides this).
 *
 * Run with:
 *   npx vitest run tests/test_rabbit_hole_worker.ts
 * or:
 *   npx jest tests/test_rabbit_hole_worker.ts
 *
 * The tests do NOT require a real Cloudflare Access setup — they forge and sign
 * JWTs with a locally generated RSA keypair and stub the JWKS fetch.
 */
import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";

// We import the worker functions. In a real vitest setup the TS config resolves
// these paths; here we assume the test file is run from the worker directory.
import {
  base64UrlDecode,
  verifyCFAccessJWT,
  purgeSessionContext,
  type Env,
} from "../src/community/gateways/rabbit_hole/src/index";
import {
  detectTokenFluctuation,
  annihilate,
  triggerWipe,
  SESSION_PREFIXES,
} from "../src/community/gateways/rabbit_hole/src/elevator_wipe";
import type { TokenState } from "../src/community/gateways/rabbit_hole/src/auth";

// ---------------------------------------------------------------------------
// In-memory KV stub
// ---------------------------------------------------------------------------

class InMemoryKV implements KVNamespace {
  private store = new Map<string, string>();

  async get(key: string): Promise<string | null> {
    return this.store.get(key) ?? null;
  }

  async put(key: string, value: string): Promise<void> {
    this.store.set(key, value);
  }

  async delete(key: string): Promise<void> {
    this.store.delete(key);
  }

  async list(opts: { prefix?: string; cursor?: string } = {}): Promise<{
    keys: { name: string }[];
    list_complete: boolean;
    cursor?: string;
  }> {
    const prefix = opts.prefix ?? "";
    const keys: { name: string }[] = [];
    for (const k of this.store.keys()) {
      if (k.startsWith(prefix)) {
        keys.push({ name: k });
      }
    }
    keys.sort((a, b) => a.name.localeCompare(b.name));
    return { keys, list_complete: true };
  }

  /** Test helper: dump all keys. */
  _all(): string[] {
    return Array.from(this.store.keys()).sort();
  }

  /** Test helper: seed a key. */
  _seed(key: string, value: string): void {
    this.store.set(key, value);
  }
}

// ---------------------------------------------------------------------------
// JWT helpers (forge + sign JWTs with a local RSA keypair)
// ---------------------------------------------------------------------------

function base64UrlEncode(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function base64UrlEncodeJson(obj: Record<string, unknown>): string {
  return base64UrlEncode(new TextEncoder().encode(JSON.stringify(obj)));
}

let cryptoKeyPair: CryptoKeyPair | null = null;
let generatedJwk: JsonWebKey | null = null;

async function ensureKeyPair(): Promise<{
  keyPair: CryptoKeyPair;
  jwk: JsonWebKey;
}> {
  if (cryptoKeyPair && generatedJwk) {
    return { keyPair: cryptoKeyPair, jwk: generatedJwk };
  }
  cryptoKeyPair = await crypto.subtle.generateKey(
    {
      name: "RSASSA-PKCS1-v1_5",
      modulusLength: 2048,
      publicExponent: new Uint8Array([1, 0, 1]),
      hash: { name: "SHA-256" },
    },
    true,
    ["sign", "verify"]
  );
  generatedJwk = await crypto.subtle.exportKey("jwk", cryptoKeyPair.publicKey);
  return { keyPair: cryptoKeyPair, jwk: generatedJwk };
}

async function signJwt(
  payload: Record<string, unknown>,
  keyPair: CryptoKeyPair,
  kid = "test-kid"
): Promise<string> {
  const header = { alg: "RS256", typ: "JWT", kid };
  const headerB64 = base64UrlEncodeJson(header);
  const payloadB64 = base64UrlEncodeJson(payload);
  const data = new TextEncoder().encode(`${headerB64}.${payloadB64}`);
  const sig = await crypto.subtle.sign(
    "RSASSA-PKCS1-v1_5",
    keyPair.privateKey,
    data
  );
  const sigB64 = base64UrlEncode(new Uint8Array(sig));
  return `${headerB64}.${payloadB64}.${sigB64}`;
}

// Stub the global fetch so getJWKS returns our local key.
function stubJwksFetch(jwk: JsonWebKey, kid: string) {
  const keys = [{ ...jwk, kid, alg: "RS256", kty: "RSA", use: "sig" }];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.endsWith("/cdn-cgi/access/certs")) {
      return new Response(JSON.stringify({ keys }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response("not found", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

// ---------------------------------------------------------------------------
// Test env
// ---------------------------------------------------------------------------

const TEAM_DOMAIN = "nousloop.cloudflareaccess.com";
let kv: InMemoryKV;
let env: Env;

beforeEach(async () => {
  kv = new InMemoryKV();
  env = {
    ELEVATOR_KV: kv,
    SECURITY_SENTRY_URL: "https://sentry.example.com/log",
    CF_ACCESS_TEAM_DOMAIN: TEAM_DOMAIN,
  };
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("Rabbit Hole Worker", () => {
  it("test_missing_cf_access_jwt_returns_401: request without CF-Access-Jwt-Assertion returns 401 with empty body", async () => {
    const { default: worker } = await import(
      "../src/community/gateways/rabbit_hole/src/index"
    );
    const req = new Request("https://rabbit.example.com/mcp/stream");
    const resp = await worker.fetch(req, env);
    expect(resp.status).toBe(401);
    const body = await resp.text();
    expect(body).toBe("");
  });

  it("test_forged_jwt_rejected: JWT with invalid signature is rejected (401, empty body)", async () => {
    const { keyPair } = await ensureKeyPair();
    const { jwk } = await ensureKeyPair();
    stubJwksFetch(jwk, "test-kid");

    // Build a valid-looking JWT but tamper with the signature.
    const payload = {
      iss: `https://${TEAM_DOMAIN}`,
      exp: Math.floor(Date.now() / 1000) + 3600,
      aud: ["test-aud"],
      sub: "user-123",
    };
    const header = { alg: "RS256", typ: "JWT", kid: "test-kid" };
    const headerB64 = base64UrlEncodeJson(header);
    const payloadB64 = base64UrlEncodeJson(payload);
    const data = new TextEncoder().encode(`${headerB64}.${payloadB64}`);
    const sig = await crypto.subtle.sign(
      "RSASSA-PKCS1-v1_5",
      keyPair.privateKey,
      data
    );
    // Tamper: flip a byte in the signature.
    const tampered = new Uint8Array(sig);
    tampered[0] = tampered[0] ^ 0xff;
    const sigB64 = base64UrlEncode(tampered);
    const forgedJwt = `${headerB64}.${payloadB64}.${sigB64}`;

    const { default: worker } = await import(
      "../src/community/gateways/rabbit_hole/src/index"
    );
    const req = new Request("https://rabbit.example.com/mcp/stream", {
      headers: { "CF-Access-Jwt-Assertion": forgedJwt },
    });
    const resp = await worker.fetch(req, env);
    expect(resp.status).toBe(401);
    expect(await resp.text()).toBe("");
  });

  it("test_expired_jwt_rejected: JWT with expired exp claim is rejected", async () => {
    const { keyPair, jwk } = await ensureKeyPair();
    stubJwksFetch(jwk, "test-kid");

    const payload = {
      iss: `https://${TEAM_DOMAIN}`,
      exp: Math.floor(Date.now() / 1000) - 3600, // expired 1h ago
      aud: ["test-aud"],
      sub: "user-123",
    };
    const jwt = await signJwt(payload, keyPair);

    const { default: worker } = await import(
      "../src/community/gateways/rabbit_hole/src/index"
    );
    const req = new Request("https://rabbit.example.com/mcp/stream", {
      headers: { "CF-Access-Jwt-Assertion": jwt },
    });
    const resp = await worker.fetch(req, env);
    expect(resp.status).toBe(401);
    expect(await resp.text()).toBe("");
  });

  it("test_wrong_issuer_rejected: JWT with incorrect iss claim is rejected", async () => {
    const { keyPair, jwk } = await ensureKeyPair();
    stubJwksFetch(jwk, "test-kid");

    const payload = {
      iss: "https://evil.example.com",
      exp: Math.floor(Date.now() / 1000) + 3600,
      aud: ["test-aud"],
      sub: "user-123",
    };
    const jwt = await signJwt(payload, keyPair);

    const { default: worker } = await import(
      "../src/community/gateways/rabbit_hole/src/index"
    );
    const req = new Request("https://rabbit.example.com/mcp/stream", {
      headers: { "CF-Access-Jwt-Assertion": jwt },
    });
    const resp = await worker.fetch(req, env);
    expect(resp.status).toBe(401);
    expect(await resp.text()).toBe("");
  });

  it("test_revoked_session_triggers_wipe: session in revoked:* triggers purgeSessionContext + 403", async () => {
    const { keyPair, jwk } = await ensureKeyPair();
    stubJwksFetch(jwk, "test-kid");

    const sessionId = "user-123";
    // Seed session state across all 5 prefixes.
    kv._seed(`session:${sessionId}`, "{}");
    kv._seed(`revoked:${sessionId}`, JSON.stringify({ revoked_at: "now" }));
    kv._seed(`tokens:${sessionId}`, "{}");
    kv._seed(`mcp_ctx:${sessionId}`, "{}");
    kv._seed(`bridge:${sessionId}`, "{}");

    const payload = {
      iss: `https://${TEAM_DOMAIN}`,
      exp: Math.floor(Date.now() / 1000) + 3600,
      aud: ["test-aud"],
      sub: sessionId,
    };
    const jwt = await signJwt(payload, keyPair);

    const { default: worker } = await import(
      "../src/community/gateways/rabbit_hole/src/index"
    );
    const req = new Request("https://rabbit.example.com/mcp/stream", {
      headers: { "CF-Access-Jwt-Assertion": jwt },
    });
    const resp = await worker.fetch(req, env);
    expect(resp.status).toBe(403);
    expect(await resp.text()).toBe("");

    // All session keys should be purged.
    for (const prefix of SESSION_PREFIXES) {
      const remaining = await kv.get(`${prefix}:${sessionId}`);
      expect(remaining).toBeNull();
    }
  });

  it("test_valid_session_redirects_to_stream: valid JWT + non-revoked session redirects to /mcp/stream", async () => {
    const { keyPair, jwk } = await ensureKeyPair();
    stubJwksFetch(jwk, "test-kid");

    const payload = {
      iss: `https://${TEAM_DOMAIN}`,
      exp: Math.floor(Date.now() / 1000) + 3600,
      aud: ["test-aud"],
      sub: "user-456",
    };
    const jwt = await signJwt(payload, keyPair);

    const { default: worker } = await import(
      "../src/community/gateways/rabbit_hole/src/index"
    );
    const req = new Request("https://rabbit.example.com/", {
      headers: { "CF-Access-Jwt-Assertion": jwt },
    });
    const resp = await worker.fetch(req, env);
    expect(resp.status).toBe(302);
    const location = resp.headers.get("location");
    expect(location).toContain("/mcp/stream");
  });

  it("test_wipe_deletes_all_session_keys: purgeSessionContext deletes ALL 5 prefixes", async () => {
    const sessionId = "user-789";
    // Seed multiple keys per prefix.
    for (const prefix of SESSION_PREFIXES) {
      kv._seed(`${prefix}:${sessionId}`, "{}");
      kv._seed(`${prefix}:${sessionId}:extra`, "{}");
    }
    // Seed an unrelated session that must NOT be touched.
    kv._seed("session:other-user", "{}");

    const deleted = await purgeSessionContext(sessionId, env);
    expect(deleted.length).toBe(SESSION_PREFIXES.length * 2);

    // The target session's keys are gone.
    for (const prefix of SESSION_PREFIXES) {
      expect(await kv.get(`${prefix}:${sessionId}`)).toBeNull();
      expect(await kv.get(`${prefix}:${sessionId}:extra`)).toBeNull();
    }
    // The unrelated session is untouched.
    expect(await kv.get("session:other-user")).toBe("{}");
  });

  it("test_session_termination_is_silent: 403 body is empty string", async () => {
    const resp = await triggerWipe("user-silent", "test_reason", env);
    expect(resp.status).toBe(403);
    expect(await resp.text()).toBe("");
  });

  it("test_no_cors_on_error_responses: 401/403 responses have no CORS headers", async () => {
    const { keyPair, jwk } = await ensureKeyPair();
    stubJwksFetch(jwk, "test-kid");

    // 401: missing JWT.
    const { default: worker } = await import(
      "../src/community/gateways/rabbit_hole/src/index"
    );
    const req401 = new Request("https://rabbit.example.com/mcp/stream");
    const resp401 = await worker.fetch(req401, env);
    expect(resp401.headers.get("Access-Control-Allow-Origin")).toBeNull();
    expect(resp401.headers.get("Access-Control-Allow-Methods")).toBeNull();

    // 403: revoked session.
    const sessionId = "user-cors";
    kv._seed(`revoked:${sessionId}`, JSON.stringify({ revoked_at: "now" }));
    const payload = {
      iss: `https://${TEAM_DOMAIN}`,
      exp: Math.floor(Date.now() / 1000) + 3600,
      aud: ["test-aud"],
      sub: sessionId,
    };
    const jwt = await signJwt(payload, keyPair);
    const req403 = new Request("https://rabbit.example.com/mcp/stream", {
      headers: { "CF-Access-Jwt-Assertion": jwt },
    });
    const resp403 = await worker.fetch(req403, env);
    expect(resp403.headers.get("Access-Control-Allow-Origin")).toBeNull();
    expect(resp403.headers.get("Access-Control-Allow-Methods")).toBeNull();
  });

  it("test_base64url_decode_roundtrip: base64UrlDecode handles url-safe encoding", () => {
    const original = "Hello NoUsClaWW +/=";
    const encoded = btoa(original).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    const decoded = new TextDecoder().decode(base64UrlDecode(encoded));
    expect(decoded).toBe(original);
  });

  it("test_detect_token_fluctuation_issuer_change: changing issuer is fluctuation", () => {
    const prev: TokenState = {
      tokenHash: "h",
      scopes: ["a"],
      issuer: "https://issuer-one",
      expiresAt: new Date(Date.now() + 3600_000).toISOString(),
      clientId: "c1",
    };
    const curr: TokenState = { ...prev, issuer: "https://issuer-two" };
    expect(detectTokenFluctuation(curr, prev)).toBe(true);
  });

  it("test_detect_token_fluctuation_scope_change: changing scopes is fluctuation", () => {
    const prev: TokenState = {
      tokenHash: "h",
      scopes: ["a", "b"],
      issuer: "https://issuer",
      expiresAt: new Date(Date.now() + 3600_000).toISOString(),
      clientId: "c1",
    };
    const curr: TokenState = { ...prev, scopes: ["a"] };
    expect(detectTokenFluctuation(curr, prev)).toBe(true);
  });

  it("test_detect_token_fluctuation_no_change: identical state is not fluctuation", () => {
    const prev: TokenState = {
      tokenHash: "h",
      scopes: ["a"],
      issuer: "https://issuer",
      expiresAt: new Date(Date.now() + 3600_000).toISOString(),
      clientId: "c1",
    };
    const curr: TokenState = { ...prev };
    expect(detectTokenFluctuation(curr, prev)).toBe(false);
  });

  it("test_annihilate_returns_deleted_keys: annihilate deletes and returns keys", async () => {
    const sessionId = "user-annihilate";
    kv._seed(`session:${sessionId}`, "{}");
    kv._seed(`tokens:${sessionId}`, "{}");
    const deleted = await annihilate(sessionId, env);
    expect(deleted.length).toBe(2);
    expect(await kv.get(`session:${sessionId}`)).toBeNull();
    expect(await kv.get(`tokens:${sessionId}`)).toBeNull();
  });
});
