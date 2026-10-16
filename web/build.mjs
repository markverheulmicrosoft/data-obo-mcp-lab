import { build } from "esbuild";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const source = dirname(fileURLToPath(import.meta.url));
const destination = resolve(source, "..", "mcp_lab", "static");
const css = (await Promise.all(["theme.css", "styles.css"].map(
  (name) => readFile(join(source, name), "utf8"),
))).join("\n");

const pages = await Promise.all(["index", "auth"].map(async (name) => {
  const template = await readFile(join(source, `${name}.html`), "utf8");
  const bundle = await build({
    entryPoints: [join(source, name === "index" ? "app.mjs" : "auth.mjs")],
    bundle: true,
    write: false,
    platform: "browser",
    format: "iife",
    target: ["es2022"],
    minify: true,
    sourcemap: false,
    legalComments: "none",
    logLevel: "warning",
  });
  const script = bundle.outputFiles[0].text.replace(/<\/script/gi, "<\\/script");
  const html = template
    .replace("<!--APP_STYLES-->", () => `<style>\n${css}\n</style>`)
    .replace("<!--APP_SCRIPT-->", () => `<script>\n${script}\n</script>`);
  if (html.includes("<!--APP_")) throw new Error(`Unresolved build marker: ${name}`);
  return { name, html };
}));

await mkdir(destination, { recursive: true });
for (const { name, html } of pages) {
  await writeFile(join(destination, `${name}.html`), html, "utf8");
  console.log(`Built mcp_lab\\static\\${name}.html (${Math.round(Buffer.byteLength(html) / 1024)} KiB)`);
}
