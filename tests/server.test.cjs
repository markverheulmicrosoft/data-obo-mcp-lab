const { test, before, after } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { spawn } = require("node:child_process");
const net = require("node:net");
const { once } = require("node:events");

let child;
let base;
let exited;

before(async () => {
  const probe = net.createServer();
  probe.listen(0, "127.0.0.1");
  await once(probe, "listening");
  const port = probe.address().port;
  await new Promise((resolve, reject) => probe.close((error) => error ? reject(error) : resolve()));
  base = `http://127.0.0.1:${port}`;
  child = spawn(process.execPath, [path.join(__dirname, "..", "server.cjs")], {
    env: { ...process.env, PORT: String(port) }, stdio: ["ignore", "pipe", "pipe"]
  });
  exited = once(child, "exit");
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Lokale server startte niet binnen 5 seconden.")), 5000);
    let output = "";
    let errors = "";
    child.stdout.on("data", (chunk) => {
      output += chunk;
      if (output.includes(`Velora leerlab: ${base}`)) { clearTimeout(timer); resolve(); }
    });
    child.stderr.on("data", (chunk) => { errors += chunk; });
    child.once("error", (error) => { clearTimeout(timer); reject(error); });
    child.once("exit", (code) => {
      clearTimeout(timer);
      reject(new Error(`Server stopte voortijdig (${code}): ${errors}`));
    });
  });
});

after(async () => {
  if (child && child.exitCode === null && child.signalCode === null) {
    child.kill();
    await exited;
  }
});

test("Server is responsief en health benoemt simulatie", async () => {
  const response = await fetch(`${base}/health`);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { status: "ok", mode: "offline-simulation" });
});

test("HTML-routes leveren het zelfstandige lab met restrictieve headers", async () => {
  for (const route of ["/", "/index.html", "/?scoutTheme=dark"]) {
    const response = await fetch(base + route);
    assert.equal(response.status, 200);
    assert.match(response.headers.get("content-type"), /text\/html/);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.match(response.headers.get("content-security-policy"), /connect-src 'none'/);
    assert.match(response.headers.get("content-security-policy"), /frame-ancestors 'none'/);
    assert.match(await response.text(), /<script id="lab-engine">/);
  }
});

test("Server deelt geen willekeurige projectbestanden", async () => {
  for (const route of ["/.env", "/package.json", "/server.cjs", "/docs/onderzoek.md", "/../README.md"]) {
    const response = await fetch(base + route);
    assert.equal(response.status, 404);
  }
});

test("Server weigert mutaties en ondersteunt HEAD zonder body", async () => {
  const post = await fetch(base, { method: "POST", body: "no-data" });
  assert.equal(post.status, 405);
  assert.equal(post.headers.get("allow"), "GET, HEAD");
  const head = await fetch(base, { method: "HEAD" });
  assert.equal(head.status, 200);
  assert.equal(await head.text(), "");
  assert.equal((await fetch(`${base}/favicon.ico`)).status, 204);
});
