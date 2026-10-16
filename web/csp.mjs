import { config } from "zod/v4";

// Apply before the MCP SDK creates schemas, avoiding even Zod's CSP-blocked eval probe.
config({ jitless: true });
