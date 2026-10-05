import assert from "node:assert/strict";
import path from "node:path";
import { pathToFileURL } from "node:url";

const workspace = process.env.WORKSPACE || "/workspace";
const { allItems } = await import(pathToFileURL(path.join(workspace, "src/service.ts")).href);
const { handleList } = await import(pathToFileURL(path.join(workspace, "src/api.ts")).href);

assert.equal(allItems().length, 5);
const res = handleList({});
assert.equal(res.status, 200);
assert.ok(Array.isArray(res.body.items));
assert.ok(res.body.items.length >= 1);
