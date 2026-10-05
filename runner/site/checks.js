// Degradation checks for the results site: each product's last 7 days against its own first week.
//
// Every check answers one question with a status: "worse" (beyond what noise explains), "watch" (moved
// the wrong way, could still be noise), "ok", "better", "few" (too few attempts to tell), "early" (the
// baseline week is still running), "changed" (a neutral measure moved a lot) or "none" (no data).
// Thresholds are strict because the page runs about forty checks a day: a stray "worse" should be rare.

export const WORSE_P = 0.002;
export const WATCH_P = 0.02;
const PRIOR = 2; // a task's baseline pass rate is shrunk toward the product's overall rate with this weight

export const mean = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
export const addDays = (d, n) => { const t = new Date(`${d}T00:00:00Z`); t.setUTCDate(t.getUTCDate() + n); return t.toISOString().slice(0, 10); };
export const graded = (a) => a.quality_status === "quality_pass" || a.quality_status === "quality_fail";
export const passed = (a) => a.quality_status === "quality_pass";

// P(X <= k), X the number of successes among independent trials with success chances ps.
export function successCdf(ps, k) {
  if (k < 0) return 0;
  let dist = [1];
  for (const p of ps) {
    const next = new Array(dist.length + 1).fill(0);
    dist.forEach((q, i) => { next[i] += q * (1 - p); next[i + 1] += q * p; });
    dist = next;
  }
  return Math.min(1, dist.slice(0, k + 1).reduce((a, b) => a + b, 0));
}

function normalCdf(z) {
  // Abramowitz and Stegun 7.1.26; error under 1.5e-7.
  const x = Math.abs(z) / Math.SQRT2, t = 1 / (1 + 0.3275911 * x);
  const erf = 1 - t * (0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429)))) * Math.exp(-x * x);
  return z >= 0 ? (1 + erf) / 2 : (1 - erf) / 2;
}

const verdict = (lowTail, highTail, gap, minGap, higherIsWorse) => {
  const [badTail, goodTail, badGap] = higherIsWorse ? [highTail, lowTail, gap] : [lowTail, highTail, -gap];
  if (badTail < WORSE_P && badGap >= minGap) return "worse";
  if (badTail < WATCH_P && badGap >= minGap) return "watch";
  if (goodTail < WATCH_P && -badGap >= minGap) return "better";
  return "ok";
};

// Pass rate on hidden tests. Each recent attempt is compared with how its own task did in the
// baseline, so a week that happens to draw harder tasks is not read as a drop.
export function passCheck(base, now) {
  const bk = base.filter(passed).length, nk = now.filter(passed).length;
  const out = { base: base.length ? bk / base.length : null, now: now.length ? nk / now.length : null,
    baseN: base.length, baseK: bk, nowN: now.length, nowK: nk };
  if (!base.length || !now.length) return { ...out, status: "none" };
  if (base.length < 3) return { ...out, status: "few" };
  const pooled = (bk + 1) / (base.length + 2);
  const byTask = new Map();
  base.forEach((a) => { const t = byTask.get(a.task_key) || { k: 0, n: 0 }; t.n += 1; t.k += passed(a) ? 1 : 0; byTask.set(a.task_key, t); });
  const ps = now.map((a) => { const t = byTask.get(a.task_key); return t ? (t.k + PRIOR * pooled) / (t.n + PRIOR) : pooled; });
  const expected = mean(ps);
  if (now.length < 3) return { ...out, expected, status: "few" };
  const low = successCdf(ps, nk), high = 1 - successCdf(ps, nk - 1);
  return { ...out, expected, p: low, status: verdict(low, high, nk / now.length - expected, 0.05, false) };
}

// A share where higher is worse (a lazy habit, attempts lost to outages): k of n now against the baseline share.
export function shareCheck(bk, bn, nk, nn, minGap = 0.05) {
  const out = { base: bn ? bk / bn : null, now: nn ? nk / nn : null, baseN: bn, baseK: bk, nowN: nn, nowK: nk };
  if (!bn || !nn) return { ...out, status: "none" };
  if (bn < 3 || nn < 3) return { ...out, status: "few" };
  const ps = new Array(nn).fill((bk + 1) / (bn + 2));
  const low = successCdf(ps, nk), high = 1 - successCdf(ps, nk - 1);
  return { ...out, p: high, status: verdict(low, high, nk / nn - bk / bn, minGap, true) };
}

// A mean (reviewer score, tests run): two-sample z test with a floor on the spread, so a baseline
// of identical scores does not turn the smallest wobble into an alarm.
export function meanCheck(xs, ys, { minGap, sdFloor, higherIsWorse = false, neutral = false }) {
  const out = { base: mean(xs), now: mean(ys), baseN: xs.length, nowN: ys.length };
  if (!xs.length || !ys.length) return { ...out, status: "none" };
  if (xs.length < 3 || ys.length < 3) return { ...out, status: "few" };
  const v = (zs, m) => Math.max(sdFloor ** 2, zs.reduce((a, z) => a + (z - m) ** 2, 0) / Math.max(1, zs.length - 1));
  const se = Math.sqrt(v(xs, out.base) / xs.length + v(ys, out.now) / ys.length);
  const z = (out.now - out.base) / se, low = normalCdf(z), high = 1 - low;
  const status = verdict(low, high, out.now - out.base, minGap, higherIsWorse);
  return { ...out, p: higherIsWorse ? high : low, status: neutral && status !== "ok" ? "changed" : status };
}

// The two windows for one product: its first 7 days, and the last 7 days after them.
export function windows(attempts, last) {
  const first = attempts.map((a) => a.date).sort()[0];
  if (!first) return null;
  const baseEnd = addDays(first, 6);
  const nowStart = [addDays(last, -6), addDays(baseEnd, 1)].sort()[1];
  return { first, baseEnd, nowStart, last, building: last <= baseEnd,
    base: attempts.filter((a) => a.date <= baseEnd), now: attempts.filter((a) => a.date >= nowStart && a.date <= last) };
}

const CATEGORY = {
  bugfix: "Bug-fix tasks", feature: "Feature tasks", refactor: "Refactoring tasks", "long-context": "Long-context tasks",
  navigation: "Code-navigation tasks", state: "State-handling tasks", build: "Build and tooling tasks",
  data: "Data-handling tasks", spec: "Spec-following tasks", python: "Python-specific tasks",
};
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

// The checks, as rows shared by every product so the map lines up.
export function rowSpecs(attempts, flags, dimensions) {
  const has = (f) => attempts.some(f);
  const rows = [
    { group: "tasks", id: "all", label: "All tasks", kind: "pass", pick: () => true },
    { group: "tasks", id: "monitor", label: "Sustained drop", hint: "drift monitor over the whole series", kind: "monitor" },
  ];
  for (const d of ["hard", "medium", "easy"]) if (has((a) => a.difficulty === d)) rows.push({ group: "tasks", id: `diff:${d}`, label: `${cap(d)} tasks`, kind: "pass", pick: (a) => a.difficulty === d });
  if (has((a) => a.source === "mined")) rows.push({ group: "tasks", id: "src:mined", label: "Real bug fixes", hint: "replayed from git history", kind: "pass", pick: (a) => a.source === "mined" });
  if (has((a) => a.source !== "mined")) rows.push({ group: "tasks", id: "src:original", label: "Benchmark tasks", hint: "written for this test", kind: "pass", pick: (a) => a.source !== "mined" });
  const cats = [...new Set(attempts.map((a) => a.category).filter(Boolean))]
    .sort((a, b) => attempts.filter((x) => x.category === b).length - attempts.filter((x) => x.category === a).length || a.localeCompare(b));
  for (const c of cats) rows.push({ group: "kinds", id: `cat:${c}`, label: CATEGORY[c] || `${cap(c)} tasks`, kind: "pass", pick: (a) => a.category === c });

  rows.push({ group: "quality", id: "q:overall", label: "Overall score", kind: "quality", value: (a) => a.quality?.overall });
  for (const [dim, question] of Object.entries(dimensions || {})) {
    rows.push({ group: "quality", id: `q:${dim}`, label: cap(dim.replace(/_/g, " ")), hint: question, kind: "quality", value: (a) => a.quality?.dims?.[dim] });
  }

  rows.push({ group: "habits", id: "h:any", label: "Any lazy habit", hint: "share of attempts flagged", kind: "share", hit: (a) => a.behavior.flags.length > 0 });
  for (const [flag, text] of Object.entries(flags || {})) {
    if (has((a) => a.behavior?.flags?.includes(flag))) rows.push({ group: "habits", id: `h:${flag}`, label: cap(text), kind: "share", hit: (a) => a.behavior.flags.includes(flag) });
  }
  rows.push({ group: "habits", id: "h:tests", label: "Test runs per attempt", hint: "fewer means less checking", kind: "work", value: (a) => a.behavior?.test_runs, minGap: 0.5, sdFloor: 0.75 });
  rows.push({ group: "habits", id: "h:calls", label: "Tool calls per attempt", hint: "how much it does; neither way is better", kind: "work", value: (a) => a.behavior?.tool_calls, minGap: 2, sdFloor: 2, neutral: true });

  rows.push({ group: "service", id: "s:lost", label: "Attempts lost", hint: "outages, usage limits, wrong model served", kind: "lost" });
  rows.push({ group: "service", id: "s:minutes", label: "Minutes per task", hint: "neither way is better", kind: "work", value: (a) => (graded(a) ? a.minutes : null), minGap: 1, sdFloor: 1, neutral: true });
  return rows;
}

export const GROUPS = {
  tasks: "Solving tasks (pass rate on hidden tests)",
  kinds: "By kind of task",
  quality: "Quality of the work (reviewer score, 1 to 5)",
  habits: "Cutting corners",
  service: "Service",
};

function monitorCheck(entry, building) {
  if (!entry) return { status: building ? "early" : "none" };
  const status = { alarm: "worse", warning: "watch", ok: "ok" }[entry.status] || (building ? "early" : "few");
  return { status, base: entry.reference?.rate ?? null, now: entry.current?.rate ?? null, message: entry.message };
}

// While the baseline week runs there is nothing to compare: a cell shows the baseline so far.
function soFar(row, base) {
  if (row.kind === "pass") {
    const g = base.filter((a) => graded(a) && row.pick(a)), k = g.filter(passed).length;
    return { base: g.length ? k / g.length : null, baseN: g.length, baseK: k, status: g.length ? "early" : "none" };
  }
  if (row.kind === "quality" || row.kind === "work") {
    const vals = base.map(row.value).filter((v) => v != null);
    return { base: mean(vals), baseN: vals.length, status: vals.length ? "early" : "none" };
  }
  const pool = row.kind === "lost" ? base : base.filter((a) => a.behavior);
  const k = row.kind === "lost" ? pool.filter((a) => !graded(a)).length : pool.filter(row.hit).length;
  return { base: pool.length ? k / pool.length : null, baseN: pool.length, baseK: k, status: pool.length ? "early" : "none" };
}

// One product's cell for every row.
export function evaluate(rows, attempts, last, monitorEntry) {
  const w = windows(attempts, last);
  if (!w) return null;
  const values = (row, xs) => xs.map(row.value).filter((v) => v != null);
  const cells = {};
  for (const row of rows) {
    let cell;
    if (row.kind === "monitor") cell = monitorCheck(monitorEntry, w.building);
    else if (w.building) cell = soFar(row, w.base);
    else if (row.kind === "pass") cell = passCheck(w.base.filter((a) => graded(a) && row.pick(a)), w.now.filter((a) => graded(a) && row.pick(a)));
    else if (row.kind === "quality") cell = meanCheck(values(row, w.base), values(row, w.now), { minGap: 0.15, sdFloor: 0.35 });
    else if (row.kind === "work") cell = meanCheck(values(row, w.base), values(row, w.now), { minGap: row.minGap, sdFloor: row.sdFloor, neutral: row.neutral });
    else if (row.kind === "share") {
      const b = w.base.filter((a) => a.behavior), n = w.now.filter((a) => a.behavior);
      cell = shareCheck(b.filter(row.hit).length, b.length, n.filter(row.hit).length, n.length);
    } else if (row.kind === "lost") {
      cell = shareCheck(w.base.filter((a) => !graded(a)).length, w.base.length, w.now.filter((a) => !graded(a)).length, w.now.length);
    }
    cells[row.id] = cell;
  }
  return { ...w, cells };
}

// The concrete places a drop shows: recent failures (with the task's baseline record), and passes whose
// reviewer score fell a full point below the same task's baseline. While the baseline is still running,
// its own failures are listed instead.
export function evidence(win) {
  if (!win) return [];
  const pool = win.building ? win.base : win.now;
  const baseByTask = new Map();
  win.base.filter(graded).forEach((a) => {
    const t = baseByTask.get(a.task_key) || { k: 0, n: 0, q: [] };
    t.n += 1; t.k += passed(a) ? 1 : 0; if (a.quality) t.q.push(a.quality.overall);
    baseByTask.set(a.task_key, t);
  });
  const items = [];
  for (const a of pool.filter(graded)) {
    const before = win.building ? null : baseByTask.get(a.task_key) || null;
    if (!passed(a)) items.push({ kind: "fail", attempt: a, before });
    else if (before && a.quality && before.q.length && mean(before.q) - a.quality.overall >= 1) {
      items.push({ kind: "quality", attempt: a, before, from: mean(before.q), to: a.quality.overall });
    }
  }
  return items.sort((x, y) => y.attempt.date.localeCompare(x.attempt.date) || x.attempt.task_key.localeCompare(y.attempt.task_key));
}

// The headline for one product: what is worse, what to watch, or that nothing moved.
export function summarize(rows, result) {
  if (!result) return { level: "none" };
  const counted = rows.filter((r) => !r.neutral);
  const of = (s) => counted.filter((r) => result.cells[r.id]?.status === s);
  if (result.building && result.cells.monitor?.status !== "worse") return { level: "early", checks: counted.length };
  const worse = of("worse"), watch = of("watch");
  const tested = counted.filter((r) => ["ok", "better", "worse", "watch"].includes(result.cells[r.id]?.status)).length;
  if (worse.length) return { level: "worse", rows: worse, watch, tested };
  if (watch.length) return { level: "watch", rows: watch, tested };
  return { level: "ok", tested, better: of("better") };
}
