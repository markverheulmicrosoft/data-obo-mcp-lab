const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");

const host = "127.0.0.1";
const port = Number(process.env.PORT || 4173);
if (!Number.isInteger(port) || port < 1 || port > 65535) {
  throw new Error("PORT moet een geheel getal tussen 1 en 65535 zijn.");
}
const page = fs.readFileSync(path.join(__dirname, "index.html"));
const server = http.createServer((request, response) => {
  const pathname = new URL(request.url, `http://${host}:${port}`).pathname;
  if (request.method !== "GET" && request.method !== "HEAD") {
    response.writeHead(405, { Allow: "GET, HEAD" });
    response.end("Method not allowed");
    return;
  }
  if (pathname === "/favicon.ico") {
    response.writeHead(204);
    response.end();
    return;
  }
  if (pathname !== "/" && pathname !== "/index.html" && pathname !== "/health") {
    response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
    response.end("Not found");
    return;
  }
  response.writeHead(200, {
    "Content-Type": pathname === "/health" ? "application/json" : "text/html; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
  });
  response.end(request.method === "HEAD" ? undefined : pathname === "/health"
    ? JSON.stringify({ status: "ok", mode: "offline-simulation" })
    : page);
});
server.on("error", (error) => {
  console.error(`Leerlab kan niet starten: ${error.message}`);
  process.exitCode = 1;
});
server.listen(port, host, () => {
  console.log(`Velora leerlab: http://${host}:${port}`);
  console.log("Uitsluitend synthetische data. Geen login, cloudcalls of echte autorisatie.");
});
