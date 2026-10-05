import assert from "node:assert/strict";
import path from "node:path";
import { pathToFileURL } from "node:url";

const workspace = process.env.WORKSPACE || "/workspace";
const mod = await import(pathToFileURL(path.join(workspace, "src/async-store.ts")).href);
const { AsyncStore } = mod;

const store = new AsyncStore();
let resolveSlow;
let resolveFast;
const slow = new Promise((resolve) => {
  resolveSlow = resolve;
});
const fast = new Promise((resolve) => {
  resolveFast = resolve;
});

const first = store.load(() => slow);
const second = store.load(() => fast);
resolveFast("new");
assert.equal(await second, "new");
assert.equal(store.current(), "new");
resolveSlow("old");
assert.equal(await first, "old");
assert.equal(store.current(), "new");
assert.equal(store.pending(), 0);
