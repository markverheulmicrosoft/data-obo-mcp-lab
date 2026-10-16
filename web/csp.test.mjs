import "./csp.mjs";
import test from "node:test";
import assert from "node:assert/strict";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { CfWorkerJsonSchemaValidator } from "@modelcontextprotocol/sdk/validation/cfworker";
import { config } from "zod/v4";

test("official MCP client validates advertised output schemas without dynamic code generation", async () => {
  assert.equal(config().jitless, true);
  const server = new Server({ name: "schema-test", version: "1.0.0" }, { capabilities: { tools: {} } });
  const client = new Client({ name: "browser-test", version: "1.0.0" }, {
    capabilities: {},
    jsonSchemaValidator: new CfWorkerJsonSchemaValidator(),
  });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  let invalid = false;
  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: [{
      name: "fabric_orders",
      inputSchema: { type: "object", properties: {} },
      outputSchema: {
        type: "object",
        properties: {
          source: { const: "fabric-warehouse" },
          authentication: { const: "delegated-obo" },
          rows: {
            type: "array",
            items: {
              type: "object",
              properties: { id: { type: "integer" }, region: { enum: ["Noord", "Zuid"] } },
              required: ["id", "region"],
            },
          },
        },
        required: ["source", "authentication", "rows"],
      },
    }],
  }));
  server.setRequestHandler(CallToolRequestSchema, async () => ({
    content: [],
    structuredContent: {
      source: "fabric-warehouse",
      authentication: "delegated-obo",
      rows: [{ id: invalid ? "not-an-integer" : 1, region: "Noord" }],
    },
  }));
  try {
    await server.connect(serverTransport);
    await client.connect(clientTransport);
    await client.listTools();
    const response = await client.callTool({ name: "fabric_orders", arguments: {} });
    assert.equal(response.structuredContent.rows[0].id, 1);
    invalid = true;
    await assert.rejects(
      client.callTool({ name: "fabric_orders", arguments: {} }),
      /validation|structured|schema/i,
    );
  } finally {
    await client.close();
    await server.close();
  }
});
