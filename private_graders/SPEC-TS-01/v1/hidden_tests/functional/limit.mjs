import assert from "node:assert/strict";
import path from "node:path";
import { pathToFileURL } from "node:url";

const workspace = process.env.WORKSPACE || "/workspace";
const { RateLimiter, checkLimit } = await import(
  pathToFileURL(path.join(workspace, "src/public-api.ts")).href
);

let now = 0;
const limiter = new RateLimiter(2, 1, () => now);
assert.equal(checkLimit(limiter, "a"), true);
assert.equal(limiter.allow("a", 1), true);
assert.equal(limiter.allow("a", 1), false);
now = 2;
assert.equal(limiter.allow("a", 1), true);
assert.equal(limiter.allow("b", 2), true);
assert.equal(limiter.allow("b", 1), false);
