import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import {
  classifyResult, compareOutcome, expectedOutcome, isAdminSession, redact,
  safeErrorCode, safeSourceUrl, validateConfig, validAccessData, validTableData,
} from "./presentation.mjs";

const config = {
  tenantId: "11111111-1111-1111-1111-111111111111",
  clientId: "22222222-2222-2222-2222-222222222222",
  gatewayUrl: "/mcp",
  scope: "api://22222222-2222-2222-2222-222222222222/access_as_user",
};
const origin = "https://lab.example";
const toolError = (code) => ({ isError: true, content: [{ type: "text", text: `Tool failed: ${code}` }] });
const table = (regions) => ({
  source: "fabric-warehouse", authentication: "delegated-obo",
  columns: ["id", "region", "product", "revenue"],
  rows: regions.map((region, index) => ({ id: index + 1, region, product: "Demo", revenue: 10 })),
});

test("configuration confines MCP to same-origin /mcp", () => {
  assert.equal(validateConfig(config, origin).gatewayUrl, `${origin}/mcp`);
  assert.equal(validateConfig({ ...config, gatewayUrl: `${origin}/mcp/` }, origin).gatewayUrl, `${origin}/mcp/`);
  for (const gatewayUrl of [
    "https://elsewhere.example/mcp", "//elsewhere.example/mcp", "/other",
    "/mcp?token=abc", "/mcp#fragment", "https://user:pass@lab.example/mcp",
    "https://lab.example.evil/mcp", "javascript:alert(1)", "/mcp/../other",
  ]) assert.throws(() => validateConfig({ ...config, gatewayUrl }, origin));
  assert.throws(() => validateConfig(config, "http://lab.example"));
  assert.doesNotThrow(() => validateConfig(config, "http://localhost:8000"));
  assert.doesNotThrow(() => validateConfig(config, "http://127.0.0.1:8000"));
  assert.throws(() => validateConfig({ ...config, tenantId: "../common" }, origin));
  assert.throws(() => validateConfig({ ...config, scope: "a b" }, origin));
});

test("source denial and gateway denial are distinct observed layers", () => {
  const source = classifyResult("fabric_margins", toolError("SOURCE_FORBIDDEN"));
  const gateway = classifyResult("fabric_finance_only", toolError("TOOL_FORBIDDEN"));
  assert.equal(source.status, "denied");
  assert.match(source.layer, /Fabric/);
  assert.equal(gateway.status, "denied");
  assert.match(gateway.layer, /Gateway/);
});

test("consent, unavailability and missing config never pass as authorization denial", () => {
  for (const code of ["OBO_FAILED", "SOURCE_UNAVAILABLE", "SOURCE_NOT_CONFIGURED"]) {
    const outcome = classifyResult("fabric_margins", toolError(code));
    assert.notEqual(outcome.status, "denied");
    assert.match(compareOutcome("fabric_margins", ["North.Reader"], outcome, null), /niet bevestigd/);
  }
  assert.equal(classifyResult("fabric_margins", toolError("unknown")).code, "TOOL_ERROR");
  assert.equal(classifyResult("fabric_margins", toolError("SOURCE_FORBIDDEN TOOL_FORBIDDEN")).code, "TOOL_ERROR");
  assert.equal(classifyResult("fabric_margins", toolError("NOT_SOURCE_FORBIDDEN")).code, "TOOL_ERROR");
});

test("unstructured or synthetic results cannot be presented as genuine Fabric success", () => {
  assert.equal(classifyResult("fabric_orders", { content: [{ type: "text", text: "SOURCE_FORBIDDEN" }] }).code, "UNEXPECTED_RESULT");
  assert.equal(classifyResult("fabric_orders", { structuredContent: { rows: [] } }).code, "UNEXPECTED_RESULT");
  assert.equal(validTableData({ ...table([]), source: "synthetic" }), false);
  assert.equal(validTableData({ ...table([]), authentication: "admin-key" }), false);
  assert.equal(validTableData({ ...table([]), rows: ["bad"] }), false);
  assert.equal(validAccessData({ user: { id: "id", roles: ["North.Reader"] }, sources: [] }), true);
  assert.equal(validAccessData({ user: { id: "id", roles: "North.Reader" }, sources: [] }), false);
});

test("RLS comparisons check both count and actual regions", () => {
  const northData = table(["Noord", "Noord", "Noord"]);
  const outcome = classifyResult("fabric_orders", { structuredContent: northData });
  assert.equal(outcome.status, "allowed");
  assert.match(compareOutcome("fabric_orders", ["North.Reader"], outcome, northData), /Komt overeen/);
  assert.match(compareOutcome("fabric_orders", ["North.Reader"], outcome, table(["Zuid", "Zuid", "Zuid"])), /Wijkt af/);
  assert.match(compareOutcome("fabric_orders", ["North.Reader"], outcome, table([])), /Wijkt af/);
  const finance = table(["Noord", "Noord", "Noord", "Zuid", "Zuid", "Zuid"]);
  assert.match(compareOutcome("fabric_orders", ["Finance.Reader"], outcome, finance), /Komt overeen/);
  assert.match(compareOutcome("fabric_orders", ["Finance.Reader"], outcome, table(Array(6).fill("Noord"))), /Wijkt af/);
  const arrayRows = { ...northData, rows: northData.rows.map((row) => Object.values(row)) };
  assert.match(compareOutcome("fabric_orders", ["North.Reader"], outcome, arrayRows), /Komt overeen/);
  assert.match(compareOutcome("fabric_margins", ["North.Reader"], outcome, northData), /bron gaf data/);
  assert.match(compareOutcome("fabric_margins", ["Finance.Reader"], classifyResult("fabric_margins", toolError("SOURCE_FORBIDDEN")), null), /echte weigering/);
});

test("unknown roles, absent identity and administrator sessions never imply user evidence", () => {
  assert.match(expectedOutcome("fabric_orders", null), /Nog onbekend/);
  assert.match(expectedOutcome("fabric_orders", []), /Geen North/);
  assert.match(expectedOutcome("fabric_orders", ["Unknown"]), /Geen North/);
  assert.match(expectedOutcome("fabric_orders", ["North.Reader", "Finance.Reader"]), /6 opgeslagen/);
  assert.match(expectedOutcome("fabric_orders", ["North"]), /Geen North.Reader/);
  assert.match(expectedOutcome("fabric_orders", ["Finance"]), /Geen North.Reader/);
  assert.match(expectedOutcome("fabric_orders", ["north.reader"]), /Geen North.Reader/);
  assert.match(expectedOutcome("fabric_orders", ["North.Reader"]), /3 opgeslagen/);
  assert.match(expectedOutcome("fabric_orders", ["Finance.Reader"]), /6 opgeslagen/);
  assert.equal(isAdminSession(["WorkspaceAdmin"]), true);
  assert.equal(isAdminSession(["North.Reader"], true), true);
  assert.match(compareOutcome("fabric_orders", ["Finance.Reader"], { status: "allowed" }, table([]), true), /Uitgesloten/);
  assert.match(compareOutcome("describe_access", ["North.Reader"], { code: "SOURCE_NOT_CONFIGURED" }, null), /niet bevestigd/);
  assert.match(compareOutcome("describe_access", null, { code: "DESCRIBE_ACCESS_MISSING" }, null), /niet bevestigd/);
});

test("raw responses redact secrets and auth messages expose only bounded codes", () => {
  const output = redact({
    access_token: "secret", nested: { Authorization: "Bearer secret", password: "secret" },
    text: "Bearer abc.def.ghi and eyJhbGciOiJub25lIn0.eyJzdWIiOiJhIn0.signature",
    rows: [{ product: "<img src=x onerror=alert(1)>" }],
  });
  assert.equal(output.access_token, "[afgeschermd]");
  assert.equal(output.nested.password, "[afgeschermd]");
  assert.doesNotMatch(output.text, /abc\.def|eyJ/);
  assert.equal(output.rows[0].product, "<img src=x onerror=alert(1)>");
  assert.equal(safeErrorCode({ errorCode: "interaction_required" }), "interaction_required");
  assert.equal(safeErrorCode({ errorCode: "error Bearer secret", message: "private" }), "REQUEST_FAILED");
  assert.equal(safeErrorCode(new Error("private access token")), "REQUEST_FAILED");
});

test("source links only use real Fabric portal hosts over HTTPS", () => {
  assert.equal(safeSourceUrl("https://app.fabric.microsoft.com/groups/test/warehouses/test"), "https://app.fabric.microsoft.com/groups/test/warehouses/test");
  assert.equal(safeSourceUrl("https://app.powerbi.com/groups/test"), "https://app.powerbi.com/groups/test");
  for (const url of [null, "javascript:alert(1)", "http://app.fabric.microsoft.com/a", "https://app.fabric.microsoft.com.evil/a", "https://a:b@app.fabric.microsoft.com/a"]) {
    assert.equal(safeSourceUrl(url), null);
  }
});

test("application has no data HTML sinks or persistent browser storage", async () => {
  const app = await readFile(new URL("./app.mjs", import.meta.url), "utf8");
  assert.doesNotMatch(app, /innerHTML|outerHTML|insertAdjacentHTML|localStorage|sessionStorage|console\./);
  assert.match(app, /BrowserCacheLocation\.MemoryStorage/);
  assert.match(app, /prompt: "select_account"/);
  assert.match(app, /redirect: "error"/);
  assert.match(app, /jsonSchemaValidator: new CfWorkerJsonSchemaValidator\(\)/);
  assert.ok(app.startsWith('import "./csp.mjs";'));
  assert.match(app, /CacheLookupPolicy\.AccessTokenAndRefreshToken/);
  assert.match(app, /httpMethod: "GET"/);
  assert.match(app, /SQL USER_NAME\(\)/);
  assert.doesNotMatch(app, /SUSER_SNAME|\.style\b|setAttribute\(["']style["']/);
  const auth = await readFile(new URL("./auth.mjs", import.meta.url), "utf8");
  assert.match(auth, /@azure\/msal-browser\/redirect-bridge/);
  const template = await readFile(new URL("./index.html", import.meta.url), "utf8");
  const callback = await readFile(new URL("./auth.html", import.meta.url), "utf8");
  assert.doesNotMatch(template + callback, /\bstyle\s*=/i);
  const ids = new Set([...template.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]));
  for (const match of app.matchAll(/\$\("([^"]+)"\)/g)) assert.ok(ids.has(match[1]), `Missing DOM id: ${match[1]}`);
});
