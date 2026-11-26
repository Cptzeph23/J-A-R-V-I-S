// Fails when src/api/schema.d.ts is not what the current OpenAPI document generates.
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const dir = mkdtempSync(join(tmpdir(), "jarvis-types-"));
const out = join(dir, "schema.d.ts");
try {
  execFileSync("npx", ["openapi-typescript", "../docs/api/openapi.yaml", "-o", out], {
    stdio: "ignore",
  });
  const fresh = readFileSync(out, "utf8");
  const current = readFileSync("src/api/schema.d.ts", "utf8");
  if (fresh !== current) {
    console.error("src/api/schema.d.ts is out of date. Run `npm run types`.");
    process.exit(1);
  }
  console.log("API types are up to date.");
} finally {
  rmSync(dir, { recursive: true, force: true });
}
