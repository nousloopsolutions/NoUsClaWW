# Rabbit Hole — VDS 60000 Cloudflare Worker

The Rabbit Hole is the **only sanctioned gateway** between the public internet
(Surface) and the NoUsClaWW MCP server (Floor). It runs as a Cloudflare Worker
at the edge and enforces the Sovereign Sockets boundary for every inbound
request.

## Purpose

- **Authenticate** every request via Cloudflare Access JWT
  (`CF-Access-Jwt-Assertion`), verified with the Web Crypto API against the
  team's JWKS.
- **Gate** revoked sessions: a revoked session triggers ruthless session
  annihilation (VDS 50000) and a `403` with an **empty body** (no information
  leak).
- **Detect** OAuth token-state fluctuation (issuer/client/scope/expiry drift)
  as a session-hijack signal.
- **Serve** the MCP stream at `/mcp/stream` via the `@modelcontextprotocol/sdk`
  server, with tool-trust verification on every connection (rug-pull defense).

## Files

| File | Responsibility |
| --- | --- |
| `src/index.ts` | Worker entry point: JWT verification, JWKS cache, session revocation check, `purgeSessionContext`. |
| `src/auth.ts` | MCP OAuth Gate: bearer-token validation, token-state recording, OAuth 2.1 upgrade hook. |
| `src/mcp.ts` | MCP server factory: tool registration, tool-trust verification, stateless request handling. |
| `src/elevator_wipe.ts` | VDS 50000 ruthless wipe: `annihilate`, `detectTokenFluctuation`, `triggerWipe`. |
| `wrangler.toml` | Cloudflare Worker config (KV binding, team domain). |

## Security features

1. **JWT signature verification** — every JWT is verified with RS256 against the
   Cloudflare Access JWKS (1-hour cache). Forged, expired, or wrong-issuer JWTs
   are rejected with `401` and an empty body.
2. **No information leak** — all error responses (`401`, `403`, `404`) have
   empty bodies and no CORS headers.
3. **Ruthless session annihilation** — revoked sessions have ALL 5 KV key
   prefixes purged (`session`, `revoked`, `tokens`, `mcp_ctx`, `bridge`) via
   `Promise.all`. No partial state survives.
4. **Token-state fluctuation detection** — any change in issuer, client id,
   scope set, or a backwards expiry jump triggers a wipe.
5. **Tool-trust verification** — the live tool manifest is compared against the
   expected manifest on every connection; drift (a rug pull) refuses the
   connection.
6. **Stateless request handling** — no per-connection state survives between
   requests; all persistent state lives in KV via the Elevator.

## Deployment

```bash
cd src/community/gateways/rabbit_hole
npm install
npx wrangler deploy
```

Before deploying, set the KV namespace id in `wrangler.toml` and (optionally)
set `SECURITY_SENTRY_URL` as a secret:

```bash
npx wrangler secret put SECURITY_SENTRY_URL
```

## Local development

```bash
npm install
npm run dev
```
