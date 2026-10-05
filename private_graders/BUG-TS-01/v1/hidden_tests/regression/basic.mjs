import assert from "node:assert/strict";
import path from "node:path";
import { pathToFileURL } from "node:url";

const workspace = process.env.WORKSPACE || "/workspace";
const { AsyncStore } = await import(pathToFileURL(path.join(workspace, "src/async-store.ts")).href);

const store = new AsyncStore();
const value = await store.load(async () => "fresh");
assert.equal(value, "fresh");
assert.equal(store.current(), "fresh");
assert.equal(store.pending(), 0);
