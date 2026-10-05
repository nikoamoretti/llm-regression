import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const workspace = process.env.WORKSPACE || "/workspace";
const source = fs.readFileSync(path.join(workspace, "src/async-store.ts"), "utf8");
assert.match(source, /class AsyncStore/);
assert.doesNotMatch(source, /from ['"]node:fs['"]/);
