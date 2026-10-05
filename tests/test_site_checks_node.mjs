import test from "node:test";
import assert from "node:assert/strict";
import { evaluate, evidence, meanCheck, passCheck, rowSpecs, shareCheck, successCdf, summarize, windows } from "../runner/site/checks.js";

const attempt = (date, task, pass, extra = {}) => ({
  date, task_key: task, quality_status: pass ? "quality_pass" : "quality_fail", difficulty: "medium",
  category: "bugfix", source: "original", behavior: { flags: [], test_runs: 2, tool_calls: 8 }, minutes: 2, ...extra,
});
const days = (start, n) => Array.from({ length: n }, (_, i) => {
  const t = new Date(`${start}T00:00:00Z`); t.setUTCDate(t.getUTCDate() + i); return t.toISOString().slice(0, 10);
});

test("successCdf matches the binomial for equal chances", () => {
  assert.ok(Math.abs(successCdf([0.5, 0.5], 0) - 0.25) < 1e-12);
  assert.ok(Math.abs(successCdf([0.5, 0.5], 1) - 0.75) < 1e-12);
  assert.equal(successCdf([0.9], -1), 0);
});

test("one failure in a week of near-perfect tasks is not called worse", () => {
  const base = days("2026-10-02", 7).flatMap((d, i) => [attempt(d, `T${i}`, true), attempt(d, `U${i}`, true)]);
  const now = days("2026-10-09", 7).flatMap((d, i) => [attempt(d, `T${i}`, i !== 3), attempt(d, `U${i}`, true)]);
  const cell = passCheck(base, now);
  assert.notEqual(cell.status, "worse");
  assert.equal(cell.nowK, 13);
});

test("a broad drop on tasks that used to pass is worse", () => {
  const base = days("2026-10-02", 7).flatMap((d, i) => [attempt(d, `T${i}`, true), attempt(d, `U${i}`, true)]);
  const now = days("2026-10-09", 7).flatMap((d, i) => [attempt(d, `T${i}`, i % 2 === 0), attempt(d, `U${i}`, i % 3 !== 0)]);
  assert.equal(passCheck(base, now).status, "worse");
});

test("drawing harder tasks is not read as a drop", () => {
  // Hard tasks failed half the time in the baseline too; a week that draws more of them is expected to pass less.
  const base = days("2026-10-02", 7).flatMap((d) => [attempt(d, "EASY", true), attempt(d, "EASY2", true), attempt(d, "HARD", d < "2026-10-06")]);
  const now = days("2026-10-09", 7).flatMap((d, i) => [attempt(d, "HARD", i % 2 === 0)]);
  assert.equal(passCheck(base, now).status, "ok");
});

test("too few recent attempts are not judged", () => {
  const base = [attempt("2026-10-02", "A", true), attempt("2026-10-03", "B", true), attempt("2026-10-04", "C", true)];
  assert.equal(passCheck(base, [attempt("2026-10-10", "A", false)]).status, "few");
});

test("shareCheck flags a habit that became common", () => {
  assert.equal(shareCheck(0, 50, 12, 50).status, "worse");
  assert.equal(shareCheck(1, 50, 2, 50).status, "ok");
});

test("meanCheck reads a full-point quality drop as worse and a small wobble as ok", () => {
  const base = Array.from({ length: 40 }, (_, i) => 4.2 + (i % 3) * 0.1);
  assert.equal(meanCheck(base, base.map((x) => x - 1), { minGap: 0.15, sdFloor: 0.35 }).status, "worse");
  assert.equal(meanCheck(base, base.map((x) => x - 0.05), { minGap: 0.15, sdFloor: 0.35 }).status, "ok");
  assert.equal(meanCheck(base, base.map((x) => x + 3), { minGap: 1, sdFloor: 1, neutral: true }).status, "changed");
});

test("windows keep the recent week clear of the baseline", () => {
  const w = windows([attempt("2026-10-02", "A", true)], "2026-10-11");
  assert.equal(w.baseEnd, "2026-10-08");
  assert.equal(w.nowStart, "2026-10-09");
  assert.equal(w.building, false);
  assert.equal(windows([attempt("2026-10-02", "A", true)], "2026-10-05").building, true);
});

test("evaluate and summarize name where a drop shows", () => {
  const base = days("2026-10-02", 7).flatMap((d, i) => [
    attempt(d, `H${i}`, true, { difficulty: "hard" }), attempt(d, `M${i}`, true), attempt(d, `N${i}`, true)]);
  const now = days("2026-10-09", 7).flatMap((d, i) => [
    attempt(d, `H${i}`, i === 0, { difficulty: "hard" }), attempt(d, `M${i}`, true), attempt(d, `N${i}`, true)]);
  const all = [...base, ...now];
  const rows = rowSpecs(all, { gave_up: "said it could not finish" }, { correctness: "Right?" });
  const result = evaluate(rows, all, "2026-10-15", null);
  assert.equal(result.cells["diff:hard"].status, "worse");
  assert.equal(result.cells["diff:medium"].status, "ok");
  const s = summarize(rows, result);
  assert.equal(s.level, "worse");
  assert.ok(s.rows.some((r) => r.id === "diff:hard"));
  const ev = evidence(result);
  assert.equal(ev.length, 6);
  assert.deepEqual(ev[0].before, { k: 1, n: 1, q: [] });
});

test("during the baseline week the verdict waits", () => {
  const all = days("2026-10-02", 4).map((d, i) => attempt(d, `T${i}`, true));
  const rows = rowSpecs(all, {}, {});
  const result = evaluate(rows, all, "2026-10-05", null);
  assert.equal(result.cells.all.status, "early");
  assert.equal(result.cells.all.base, 1);
  assert.equal(summarize(rows, result).level, "early");
});
