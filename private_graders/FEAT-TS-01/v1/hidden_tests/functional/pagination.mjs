import assert from "node:assert/strict";
import path from "node:path";
import { pathToFileURL } from "node:url";

const workspace = process.env.WORKSPACE || "/workspace";
const { listItems } = await import(pathToFileURL(path.join(workspace, "src/service.ts")).href);
const { handleList } = await import(pathToFileURL(path.join(workspace, "src/api.ts")).href);
const { renderList } = await import(pathToFileURL(path.join(workspace, "src/ui.ts")).href);

const page2 = listItems({ page: 2, pageSize: 2 });
assert.deepEqual(page2.items.map((item) => item.name), ["charlie", "delta"]);
assert.equal(page2.total, 5);
assert.equal(page2.totalPages, 3);

const empty = listItems({ page: 9, pageSize: 2 });
assert.deepEqual(empty.items, []);
assert.equal(empty.page, 9);

const invalid = listItems({ page: 0, pageSize: 2 });
assert.deepEqual(invalid.items, []);

const api = handleList({ page: "2", pageSize: "2" });
assert.equal(api.status, 200);
assert.equal(api.body.page, 2);
assert.deepEqual(api.body.items.map((item) => item.name), ["charlie", "delta"]);

const html = renderList({ page: "3", pageSize: "2" });
assert.match(html, /Page 3 of 3/);
assert.match(html, /echo/);
assert.doesNotMatch(html, /alpha/);
