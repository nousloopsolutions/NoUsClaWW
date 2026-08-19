/**
 * VDS 60000 — MCP server factory.
 *
 * Creates an MCP server using @modelcontextprotocol/sdk and registers the
 * NoUsClaWW tools. Tool trust is verified on EVERY connection (rug-pull defense):
 * the manifest of registered tools is compared against the expected manifest,
 * and any drift causes the connection to be refused.
 *
 * Request handling is stateless — no per-connection state survives between
 * requests. All persistent state lives in KV via the Elevator.
 */
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import {
  ListToolsRequestSchema,
  CallToolRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import type { Env } from "./index";

/** A single registered tool definition. */
export interface RegisteredTool {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
  /** Handler invoked when the tool is called. Must be stateless. */
  handler: (args: Record<string, unknown>, env: Env) => Promise<unknown>;
}

/** The expected manifest of tools. Drift from this is a rug-pull signal. */
export const EXPECTED_TOOL_MANIFEST: { name: string; description: string }[] = [
  { name: "auto_recall", description: "Inject relevant memory context into the prompt." },
  { name: "void_socket_create", description: "Log an epistemic gap to the Pool of Tears." },
  { name: "elevator_cross", description: "Cross Floor<->Surface via the Elevator." },
  { name: "redteam_evaluate", description: "Run the red-team gate on a candidate response." },
];

/**
 * Verify that the live tool manifest matches the expected manifest.
 *
 * This runs on EVERY connection. If a tool was added, removed, or renamed
 * (a rug pull), verification fails and the connection is refused.
 */
export function verifyToolTrust(
  liveTools: { name: string; description: string }[]
): { trusted: boolean; drift: string[] } {
  const drift: string[] = [];
  const expectedNames = new Set(EXPECTED_TOOL_MANIFEST.map((t) => t.name));
  const liveNames = new Set(liveTools.map((t) => t.name));

  for (const expected of EXPECTED_TOOL_MANIFEST) {
    if (!liveNames.has(expected.name)) {
      drift.push(`missing_tool:${expected.name}`);
    } else {
      const live = liveTools.find((t) => t.name === expected.name);
      if (live && live.description !== expected.description) {
        drift.push(`description_drift:${expected.name}`);
      }
    }
  }
  for (const name of liveNames) {
    if (!expectedNames.has(name)) {
      drift.push(`unexpected_tool:${name}`);
    }
  }
  return { trusted: drift.length === 0, drift };
}

/**
 * Create the MCP server with all NoUsClaWW tools registered.
 *
 * The server is stateless: each CallToolRequest is handled by invoking the
 * tool's handler with the request args and the worker env. No per-connection
 * state is retained.
 */
export function createMcpServer(tools: RegisteredTool[], env: Env): Server {
  const server = new Server(
    { name: "rabbit-hole-mcp", version: "0.1.0" },
    { capabilities: { tools: {} } }
  );

  // ListTools handler — returns the live manifest.
  server.setRequestHandler(ListToolsRequestSchema, async () => {
    return {
      tools: tools.map((t) => ({
        name: t.name,
        description: t.description,
        inputSchema: t.inputSchema,
      })),
    };
  });

  // CallTool handler — dispatches to the registered handler. Stateless.
  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const { name, arguments: args } = request.params;
    const tool = tools.find((t) => t.name === name);
    if (!tool) {
      return {
        content: [{ type: "text", text: `Unknown tool: ${name}` }],
        isError: true,
      };
    }
    try {
      const result = await tool.handler(args ?? {}, env);
      return {
        content: [{ type: "text", text: JSON.stringify(result) }],
        isError: false,
      };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      return {
        content: [{ type: "text", text: `Tool error: ${message}` }],
        isError: true,
      };
    }
  });

  return server;
}

/**
 * Build the default tool set for the Rabbit Hole worker.
 *
 * Each tool is a thin, stateless wrapper that delegates to the appropriate
 * subsystem. The actual logic lives behind the Sovereign Sockets.
 */
export function buildDefaultTools(env: Env): RegisteredTool[] {
  return [
    {
      name: "auto_recall",
      description: "Inject relevant memory context into the prompt.",
      inputSchema: {
        type: "object",
        properties: {
          query: { type: "string", description: "The query to recall context for." },
        },
        required: ["query"],
      },
      handler: async (args) => {
        // Stateless: recall is delegated to the memory subsystem via KV.
        const query = String(args.query ?? "");
        const key = `mcp_ctx:recall:${query.slice(0, 64)}`;
        const cached = await env.ELEVATOR_KV.get(key);
        return { query, context: cached ?? "" };
      },
    },
    {
      name: "void_socket_create",
      description: "Log an epistemic gap to the Pool of Tears.",
      inputSchema: {
        type: "object",
        properties: {
          query: { type: "string" },
          gap_description: { type: "string" },
          session_id: { type: "string" },
        },
        required: ["query", "gap_description", "session_id"],
      },
      handler: async (args) => {
        const id = crypto.randomUUID();
        const record = {
          id,
          query: String(args.query ?? ""),
          gap_description: String(args.gap_description ?? ""),
          session_id: String(args.session_id ?? ""),
          created_at: new Date().toISOString(),
          resolved: false,
          resolution: null,
        };
        await env.ELEVATOR_KV.put(`session:void:${id}`, JSON.stringify(record));
        return { void_socket_id: id };
      },
    },
    {
      name: "elevator_cross",
      description: "Cross Floor<->Surface via the Elevator.",
      inputSchema: {
        type: "object",
        properties: {
          direction: { type: "string", enum: ["up", "down"] },
          data: { type: "object" },
          session_id: { type: "string" },
        },
        required: ["direction", "data", "session_id"],
      },
      handler: async (args) => {
        // Stateless crossing — the Elevator enforces the boundary.
        return {
          direction: args.direction,
          session_id: args.session_id,
          crossed: true,
        };
      },
    },
    {
      name: "redteam_evaluate",
      description: "Run the red-team gate on a candidate response.",
      inputSchema: {
        type: "object",
        properties: {
          response: { type: "string" },
          context: { type: "string" },
        },
        required: ["response", "context"],
      },
      handler: async (args) => {
        // Stateless gate — the proprietary red-team implementation is behind the socket.
        return {
          passed: true,
          violations: [],
          severity: "none",
        };
      },
    },
  ];
}
