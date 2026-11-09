// Prepares secrets from ../.env without printing them.
// node scripts/sync_secrets.mjs local   writes .dev.vars for wrangler dev.
// node scripts/sync_secrets.mjs remote  stores them as encrypted Worker secrets.

import { spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { appendFileSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const edge = join(dirname(fileURLToPath(import.meta.url)), "..");
const envPath = join(edge, "..", ".env");
const env = {};
for (const line of readFileSync(envPath, "utf8").split(/\r?\n/)) {
  const match = line.match(/^([A-Z_]+)=(.*)$/);
  if (match) env[match[1]] = match[2].trim().replace(/^["']|["']$/g, "");
}
for (const name of ["ACCESS_PASSWORD", "SESSION_SECRET"]) {
  if (!env[name]) {
    env[name] = randomBytes(name === "SESSION_SECRET" ? 32 : 12).toString("base64url");
    appendFileSync(envPath, `\n${name}=${env[name]}\n`);
    console.log(`Created ${name} and saved it to .env.`);
  }
}
const NAMES = ["JEV_API_KEY", "GROQ_API_KEY", "CARTESIA_API_KEY", "ACCESS_PASSWORD", "SESSION_SECRET"];
const missing = NAMES.filter(name => !env[name]);
if (missing.length) {
  console.error(`Add these to .env first: ${missing.join(", ")}`);
  process.exit(1);
}
const lines = NAMES.map(name => `${name}=${env[name]}`).join("\n") + "\n";

if (process.argv[2] === "remote") {
  const file = join(edge, ".secrets.tmp");
  writeFileSync(file, JSON.stringify(Object.fromEntries(NAMES.map(name => [name, env[name]]))), { mode: 0o600 });
  let status = 1;
  try {
    status = spawnSync("npx", ["wrangler", "secret", "bulk", ".secrets.tmp"], { cwd: edge, stdio: "inherit", shell: true }).status ?? 1;
  } finally {
    unlinkSync(file);
  }
  process.exit(status);
} else {
  writeFileSync(join(edge, ".dev.vars"), lines, { mode: 0o600 });
  console.log("Wrote .dev.vars for local testing.");
}
