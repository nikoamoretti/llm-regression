import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const workspace = process.env.WORKSPACE || "/workspace";
const api = fs.readFileSync(path.join(workspace, "src/api.ts"), "utf8");
const ui = fs.readFileSync(path.join(workspace, "src/ui.ts"), "utf8");
assert.match(api, /pageSize/);
assert.match(ui, /Page /);
