import assert from "node:assert/strict";
import path from "node:path";
import { pathToFileURL } from "node:url";

const workspace = process.env.WORKSPACE || "/workspace";
const api = await import(pathToFileURL(path.join(workspace, "src/public-api.ts")).href);
assert.equal(typeof api.RateLimiter, "function");
assert.equal(typeof api.checkLimit, "function");
