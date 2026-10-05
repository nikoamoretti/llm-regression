import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const workspace = process.env.WORKSPACE || "/workspace";
const publicApi = fs.readFileSync(path.join(workspace, "src/public-api.ts"), "utf8");
assert.match(publicApi, /export \{ RateLimiter \}/);
assert.match(publicApi, /checkLimit/);
assert.ok(fs.existsSync(path.join(workspace, "src/internal/rate-limiter.ts")));
