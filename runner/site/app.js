// Renders data.json (written by `python -m runner site`) one model at a time, in the order a reader asks:
// is it getting worse (the answer), three numbers, every day's results, what needs attention, then details.
import { addDays, evaluate, evidence, graded, passed, rowSpecs, summarize } from "./checks.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (x) => (x == null ? "–" : `${Math.round(x * 100)}%`);
const day = (d, opts = {}) => new Date(`${d}T00:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC", ...opts });
const longDay = (d) => day(d, { weekday: "long", month: "long" });
// Plain words for each check's state; the thresholds behind them are in "How this works".
const STATUS = {
  worse: "Clear drop", watch: "Possible drop", ok: "Normal", better: "Better", few: "Too little data",
  early: "Learning", changed: "Changed", none: "–",
};
const ANSWER = {
  worse: "Yes, it's getting worse", watch: "Maybe. Something dipped", ok: "No sign of it", early: "Too early to say", none: "Not tracked yet",
};
const GROUP_TITLES = {
  tasks: "Solving tasks", kinds: "By kind of task", quality: "Code quality (reviewer score, 1 to 5)", habits: "Cutting corners", service: "Service",
};
const WHY = {
  usage_limit: "the plan's usage limit was reached", auth: "sign-in failed", model_mismatch: "a different model was served",
  effort_mismatch: "a different reasoning level was served", web_access: "the agent reached the web",
  isolation_unavailable: "the product was not set up", container_restart: "the machine restarted",
  no_result_event: "the service stopped without a result (often an outage)", timeout: "it ran out of time",
};
const KIND = { bugfix: "bug fix", "long-context": "long context" };
const isShare = (row) => row.kind === "pass" || row.kind === "share" || row.kind === "lost";
const list = (xs) => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`);

let DATA, PRODUCTS, LAST, current;

fetch("data.json", { cache: "no-store" })
  .then((r) => { if (!r.ok) throw new Error(`data.json: HTTP ${r.status}`); return r.json(); })
  .then(init)
  .catch((err) => { $("question").textContent = "The latest results could not be loaded."; $("a-why").textContent = err.message; });

function init(data) {
  DATA = data;
  // One series per model: the effort of its newest run. Runs at other efforts stay in the run log.
  PRODUCTS = DATA.products.map((p) => {
    const runs = DATA.runs.filter((r) => r.track === p.track);
    const effort = runs.length ? runs[runs.length - 1].effort : null;
    const attempts = DATA.attempts.filter((a) => a.track === p.track && a.effort === effort);
    return { ...p, key: p.track.startsWith("cursor") ? "grok" : "opus", effort, attempts, runs };
  });
  LAST = DATA.attempts.map((a) => a.date).sort().pop();
  const fmt = (d) => new Date(d).toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
  $("stamp").textContent = `Updated ${fmt(DATA.generated_at)} UTC` + (DATA.next_run ? ` · next update ${fmt(DATA.next_run)}` : "");

  const tabs = $("tabs");
  tabs.innerHTML = PRODUCTS.map((p) => `<button type="button" role="tab" id="tab-${p.key}" aria-controls="model" data-key="${p.key}">`
    + `<b>${esc(p.name)}</b><span>${esc(p.via)}</span></button>`).join("");
  tabs.addEventListener("click", (e) => { const b = e.target.closest("[role=tab]"); if (b) location.hash = b.dataset.key; });
  tabs.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const i = PRODUCTS.indexOf(current), next = PRODUCTS[(i + (e.key === "ArrowRight" ? 1 : PRODUCTS.length - 1)) % PRODUCTS.length];
    location.hash = next.key;
    $(`tab-${next.key}`).focus();
  });
  window.addEventListener("hashchange", route);
  let timer;
  window.addEventListener("resize", () => { clearTimeout(timer); timer = setTimeout(() => current?.attempts.length && drawChart(current), 150); });
  route();
}

// #opus or #grok picks the model; #opus/TASK-ID opens that task under "Every task".
function route() {
  const [key, task] = location.hash.slice(1).split("/");
  const p = PRODUCTS.find((x) => x.key === key) || current || PRODUCTS.find((x) => x.attempts.length) || PRODUCTS[0];
  if (p !== current) show(p);
  if (task) {
    const target = document.getElementById(`task-${decodeURIComponent(task)}`);
    if (target) { $("tasks-box").open = true; target.open = true; target.scrollIntoView({ block: "start" }); }
  }
}

function show(p) {
  current = p;
  PRODUCTS.forEach((x) => {
    const tab = $(`tab-${x.key}`);
    tab.setAttribute("aria-selected", String(x === p));
    tab.tabIndex = x === p ? 0 : -1;
  });
  document.title = `${p.name} · Nerf Watch`;
  $("model").setAttribute("aria-labelledby", `tab-${p.key}`);
  $("question").textContent = `Is ${p.name} getting worse?`;
  const live = p.attempts.length > 0;
  document.querySelector(".needs-data").hidden = !live;
  if (!live) {
    $("answer").className = "answer none";
    $("a-word").textContent = ANSWER.none;
    $("a-why").textContent = p.setup;
    $("progress").hidden = true;
    return;
  }
  const rows = rowSpecs(p.attempts, DATA.flags, DATA.dimensions);
  const monitor = (DATA.monitor || []).find((m) => m.series === `${p.model}/${p.effort}/${p.track}`);
  const result = evaluate(rows, p.attempts, LAST, monitor);
  const days = dailyCounts(p);
  Object.assign(p, { rows, result, summary: summarize(rows, result), evidence: evidence(result), days });
  p.selected = [...days].reverse().find((x) => x.total)?.d;
  drawAnswer(p);
  drawStats(p);
  drawChart(p);
  drawDay(p);
  drawAttention(p);
  drawChecks(p);
  drawTasks(p);
  drawRuns(p);
}

const title = (key) => DATA.task_titles?.[key] || key;
const window7 = (p) => p.attempts.filter((a) => a.date > addDays(LAST, -7) && a.date <= LAST);

function dailyCounts(p) {
  const first = p.attempts.map((a) => a.date).sort()[0];
  const out = [];
  for (let d = first; d <= LAST; d = addDays(d, 1)) {
    const xs = p.attempts.filter((a) => a.date === d), g = xs.filter(graded);
    out.push({ d, attempts: xs, solved: g.filter(passed).length, failed: g.length - g.filter(passed).length, lost: xs.length - g.length, total: xs.length });
  }
  return out;
}

// ---- How a check reads in a sentence
function value(row, cell, side) {
  const v = cell[side];
  if (v == null) return "–";
  return isShare(row) || row.kind === "monitor" ? pct(v) : v.toFixed(1);
}
function sentence(row, cell) {
  const b = value(row, cell, "base"), n = value(row, cell, "now");
  switch (row.kind) {
    case "pass": return `solved ${cell.nowK} of ${cell.nowN} in the last 7 days (${n}), against ${cell.baseK} of ${cell.baseN} (${b}) in its first week.`;
    case "quality": return `scored ${n} in the last 7 days, against ${b} in its first week.`;
    case "share": return `seen in ${n} of runs in the last 7 days, against ${b} in its first week.`;
    case "lost": return `${n} of runs lost in the last 7 days, against ${b} in its first week.`;
    case "work": return `${n} per run in the last 7 days, against ${b} in its first week.`;
    default: return cell.message ? `${cell.message}.` : "the drift monitor saw a sustained drop.";
  }
}
const short = (row, cell) => `${row.label.charAt(0).toLowerCase()}${row.label.slice(1)} (${value(row, cell, "base")} → ${value(row, cell, "now")})`;

// ---- 1. The answer
function drawAnswer(p) {
  const { summary: s, result: r } = p, c = r.cells, week = window7(p).filter(graded), k = week.filter(passed).length;
  $("answer").className = `answer ${s.level}`;
  $("a-word").textContent = ANSWER[s.level];
  let why;
  if (s.level === "early") {
    why = `Nerf Watch needs one full week to learn what normal looks like for ${p.name}. So far it has solved ${k} of ${week.length} tasks (${pct(week.length ? k / week.length : null)}).`;
  } else if (s.level === "worse") {
    why = `It is doing clearly worse than in its own first week on ${list(s.rows.slice(0, 2).map((row) => short(row, c[row.id])))}`
      + `${s.rows.length > 2 ? `, and ${s.rows.length - 2} more` : ""}. The details are below.`;
  } else if (s.level === "watch") {
    why = `Compared with its first week, ${list(s.rows.slice(0, 2).map((row) => short(row, c[row.id])))} dipped, but that could still be chance. A bigger or longer drop would make it clear.`;
  } else {
    why = `In the last 7 days it solved ${k} of ${week.length} tasks (${pct(week.length ? k / week.length : null)}), in line with its first week (${pct(c.all.base)}). Nothing else changed beyond normal day-to-day variation.`;
  }
  $("a-why").textContent = why;
  const prog = $("progress");
  prog.hidden = s.level !== "early";
  if (s.level === "early") {
    const n = Math.min(7, Math.round((Date.parse(LAST) - Date.parse(r.first)) / 864e5) + 1);
    prog.innerHTML = `<div class="steps" role="img" aria-label="Day ${n} of 7">${Array.from({ length: 7 }, (_, i) => `<span class="${i < n ? "on" : ""}"></span>`).join("")}</div>`
      + `<p>Day ${n} of 7 of its first week. The first answer comes on <b>${longDay(addDays(r.baseEnd, 1))}</b>.</p>`;
  }
}

// ---- 2. Three numbers
function drawStats(p) {
  const r = p.result, c = r.cells, week = window7(p), g = week.filter(graded), k = g.filter(passed).length;
  const scores = g.filter((a) => a.quality).map((a) => a.quality.overall);
  const score = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null;
  const firstWeek = (txt) => (r.building ? "This is still its first week" : `First week: ${txt}`);
  const flag = (cell) => (cell && ["worse", "watch", "better"].includes(cell.status) ? ` · <span class="flag ${cell.status}">${STATUS[cell.status]}</span>` : "");
  const tiles = [
    { label: "Tasks solved", value: pct(g.length ? k / g.length : null), sub: `${k} of ${g.length} in the last 7 days`, ref: firstWeek(pct(c.all.base)), cell: c.all },
    { label: "Code quality", value: score == null ? "–" : `${score.toFixed(1)}<small> / 5</small>`, sub: "reviewer score, last 7 days",
      ref: firstWeek(c["q:overall"]?.base == null ? "–" : c["q:overall"].base.toFixed(1)), cell: c["q:overall"] },
    { label: "Runs lost", value: String(week.length - g.length), sub: "outages or usage limits, last 7 days. Not counted as failures.",
      ref: firstWeek(String(c["s:lost"]?.baseK ?? 0)), cell: c["s:lost"] },
  ];
  $("stats").innerHTML = tiles.map((t) => `<article class="stat"><h3>${t.label}</h3><p class="v">${t.value}</p>`
    + `<p class="sub">${esc(t.sub)}</p><p class="ref">${esc(t.ref)}${flag(t.cell)}</p></article>`).join("");
}

// ---- 3. Every day's results: a column per day, solved at the bottom, failed and lost stacked above.
function drawChart(p) {
  const host = $("chart"), days = p.days, n = days.length, r = p.result;
  const W = Math.max(280, Math.round(host.clientWidth || 700)), narrow = W < 520;
  const H = narrow ? 180 : 210, L = 26, R = 6, T = 22, B = 26;
  const slot = (W - L - R) / n, bw = Math.min(24, Math.max(8, slot * 0.62));
  const maxN = Math.max(1, ...days.map((x) => x.total));
  const step = [1, 2, 5, 10, 20, 50].find((s) => maxN / s <= 4) || 100;
  const top = Math.ceil(maxN / step) * step;
  const y = (v) => T + (1 - v / top) * (H - T - B);
  const cx = (i) => L + slot * (i + 0.5);
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="group" aria-label="${esc(p.name)}: tasks solved and failed each day">`;
  const baseLast = days.findIndex((x) => x.d > r.baseEnd);
  const bEnd = baseLast < 0 ? n : baseLast;
  svg += `<rect class="band" x="${L}" y="${T}" width="${slot * bEnd}" height="${H - T - B}"/>`;
  svg += `<text class="band-label" x="${L + 4}" y="${T - 8}">First week</text>`;
  Array.from({ length: top / step + 1 }, (_, i) => i * step).forEach((t) => {
    svg += `<line class="grid-line" x1="${L}" x2="${W - R}" y1="${y(t)}" y2="${y(t)}"/><text class="tick" x="${L - 6}" y="${y(t) + 4}" text-anchor="end">${t}</text>`;
  });
  const every = Math.max(1, Math.ceil(n / (narrow ? 5 : 10)));
  days.forEach((x, i) => {
    const sel = x.d === p.selected;
    if (sel) svg += `<rect class="sel" x="${cx(i) - slot / 2 + 1}" y="${T - 2}" width="${slot - 2}" height="${H - T - B + 4}"/>`;
    let base = 0;
    // Solved is plain ink for every model, so the only colour in the chart is a failure.
    const segs = [["solved", x.solved, "var(--solved)"], ["failed", x.failed, "var(--fail)"], ["lost", x.lost, "var(--lost)"]].filter((s) => s[1] > 0);
    segs.forEach(([name, v, fill], si) => {
      // A 2px surface gap separates a segment from the one below it.
      const x0 = cx(i) - bw / 2, yb = y(base) - (si > 0 ? 2 : 0), yt = Math.min(yb - 1, y(base + v));
      const h = yb - yt;
      if (si === segs.length - 1) {
        const rr = Math.min(2, h, bw / 2);
        svg += `<path class="seg ${name}" fill="${fill}" d="M${x0},${yt + h} V${yt + rr} Q${x0},${yt} ${x0 + rr},${yt} H${x0 + bw - rr} Q${x0 + bw},${yt} ${x0 + bw},${yt + rr} V${yt + h} Z"/>`;
      } else {
        svg += `<rect class="seg ${name}" fill="${fill}" x="${x0}" y="${yt}" width="${bw}" height="${h}"/>`;
      }
      base += v;
    });
    if (!x.total) svg += `<line class="none" x1="${cx(i) - bw / 2}" x2="${cx(i) + bw / 2}" y1="${y(0) - 1}" y2="${y(0) - 1}"/>`;
    if (i % every === 0 || i === n - 1 || sel) svg += `<text class="tick${sel ? " on" : ""}" x="${cx(i)}" y="${H - 8}" text-anchor="middle">${day(x.d)}</text>`;
    const label = x.total ? `${day(x.d)}: ${x.solved} solved, ${x.failed} failed${x.lost ? `, ${x.lost} lost` : ""}` : `${day(x.d)}: no run`;
    svg += `<rect class="hit" data-i="${i}" x="${cx(i) - slot / 2}" y="0" width="${slot}" height="${H}" fill="transparent"`
      + `${x.total ? ` tabindex="0" role="button" aria-pressed="${sel}"` : ""} aria-label="${esc(label)}"/>`;
  });
  host.innerHTML = svg + "</svg>";

  const tip = $("tooltip");
  host.querySelectorAll(".hit").forEach((el) => {
    const x = days[Number(el.dataset.i)];
    const pick = () => { if (!x.total) return; p.selected = x.d; drawChart(p); drawDay(p); host.querySelector(`.hit[data-i="${el.dataset.i}"]`)?.focus(); };
    el.addEventListener("click", pick);
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); }
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        const step = e.key === "ArrowRight" ? 1 : -1;
        let j = Number(el.dataset.i) + step;
        while (days[j] && !days[j].total) j += step;
        if (days[j]) { p.selected = days[j].d; drawChart(p); drawDay(p); host.querySelector(`.hit[data-i="${j}"]`)?.focus(); }
      }
    });
    el.addEventListener("pointermove", (evt) => {
      tip.innerHTML = x.total ? `<b>${longDay(x.d)}</b><br>${x.solved} solved · ${x.failed} failed${x.lost ? ` · ${x.lost} lost` : ""}<br><span class="dim">Click to see the tasks</span>`
        : `<b>${longDay(x.d)}</b><br>No run that day`;
      tip.hidden = false;
      tip.style.left = `${Math.max(8, Math.min(evt.clientX + 14, window.innerWidth - tip.offsetWidth - 8))}px`;
      tip.style.top = `${evt.clientY + 14}px`;
    });
    el.addEventListener("pointerleave", () => { tip.hidden = true; });
  });
}

function taskItem(a, record) {
  const timedOut = a.error_code === "timeout";
  const mark = !graded(a) ? `<span class="res">Not counted</span>` : passed(a) ? `<span class="res ok">Solved</span>`
    : `<span class="res fail">${timedOut ? "Timed out" : "Failed"}</span>`;
  const why = !graded(a) ? `<p>Not counted: ${esc(WHY[a.error_code] || a.error_code || "unknown error")}.</p>`
    : timedOut ? "<p>It ran out of time and was stopped at the task's time limit, so it counts as failed. A run has to finish in time to count.</p>" : "";
  const flags = a.behavior?.flags?.length ? `<p>Flagged: ${a.behavior.flags.map((f) => esc(DATA.flags[f] || f)).join("; ")}.</p>` : "";
  return `<details class="item"><summary>${mark}<span class="t">${esc(title(a.task_key))}</span>`
    + `<span class="meta">${esc(a.task_key)} · ${esc(a.difficulty)}${record ? ` · ${record}` : ""}</span></summary>`
    + `<div class="body">${why}${a.quality ? `<p>${esc(a.quality.summary)}</p><p class="dim">Code quality ${a.quality.overall.toFixed(1)} / 5</p>` : ""}${flags}</div></details>`;
}

function drawDay(p) {
  const x = p.days.find((d) => d.d === p.selected);
  if (!x) { $("day").innerHTML = ""; return; }
  const order = (a) => (!graded(a) ? 1 : passed(a) ? 2 : 0);
  const items = [...x.attempts].sort((a, b) => order(a) - order(b) || a.task_key.localeCompare(b.task_key));
  $("day").innerHTML = `<h3>${longDay(x.d)}: ${x.solved} of ${x.solved + x.failed} solved${x.lost ? `, ${x.lost} lost` : ""}</h3>`
    + `<div class="items">${items.map((a) => taskItem(a)).join("")}</div>`;
}

// ---- 4. What needs attention: only what moved, in sentences, then the failed tasks behind it.
function drawAttention(p) {
  const { summary: s, result: r } = p, c = r.cells;
  // Clear drops before possible ones; within each, the order of the checks (overall first).
  const moved = p.rows.filter((row) => ["worse", "watch"].includes(c[row.id]?.status))
    .sort((x, y) => (c[x.id].status === "worse" ? 0 : 1) - (c[y.id].status === "worse" ? 0 : 1));
  const fails = p.evidence;
  const hide = (i, n) => (i >= n ? " hidden" : "");
  const more = (n, shown) => (n > shown ? `<button type="button" class="more-btn">Show ${n - shown} more</button>` : "");
  let html = "";
  if (moved.length) {
    html += `<div class="group"><ul class="alerts">${moved.map((row, i) => `<li${hide(i, 3)}><span class="flag ${c[row.id].status}">${STATUS[c[row.id].status]}</span>`
      + `<p><b>${esc(row.label)}</b>: ${esc(sentence(row, c[row.id]))}</p></li>`).join("")}</ul>${more(moved.length, 3)}</div>`;
  }
  if (fails.length) {
    const head = r.building ? "Tasks it failed so far" : "Tasks it failed in the last 7 days";
    html += `<div class="group"><h3 class="sub-h">${head}</h3><div class="items">${fails.map(({ kind, attempt: a, before, from, to }, i) => {
      const record = before ? (kind === "fail" ? `passed ${before.k} of ${before.n} in its first week` : `quality ${from.toFixed(1)} → ${to.toFixed(1)}`) : "";
      return taskItem(a, `${day(a.date)}${record ? ` · ${record}` : ""}`).replace("<details", `<details${hide(i, 5)}`);
    }).join("")}</div>${more(fails.length, 5)}</div>`;
  }
  if (!html) {
    html = `<p class="calm">${r.building ? "Nothing yet: no task has failed so far." : `Nothing unusual. All ${s.tested} checks are normal, and no task failed in the last 7 days.`}</p>`;
  } else if (!moved.length && !r.building) {
    html = `<p class="calm">No check moved beyond normal variation. These are the failures behind the numbers.</p>` + html;
  }
  $("attention").innerHTML = html;
  $("attention").querySelectorAll(".more-btn").forEach((b) => b.addEventListener("click", () => {
    b.closest(".group").querySelectorAll("[hidden]").forEach((el) => { el.hidden = false; });
    b.remove();
  }));
}

// ---- 5. Details
function drawChecks(p) {
  const r = p.result;
  const shown = p.rows.filter((row) => r.cells[row.id]?.status !== "none");
  const moved = shown.filter((row) => ["worse", "watch"].includes(r.cells[row.id].status)).length;
  $("checks-sum").textContent = `${shown.length} checks` + (moved ? `, ${moved} moved` : r.building ? ", learning" : ", all normal");
  $("checks-note").textContent = r.building
    ? `Its first week runs ${day(r.first)} – ${day(r.baseEnd)}. Until it ends, each check shows the first week so far.`
    : `First week ${day(r.first)} – ${day(r.baseEnd)}, against the last 7 days, ${day(r.nowStart)} – ${day(r.last)}.`;
  const count = (row, cell, side) => {
    const n = cell[`${side}N`], k = cell[`${side}K`];
    return n == null ? "" : isShare(row) ? `${k} of ${n}` : `${n} ${row.kind === "quality" ? "scored" : "runs"}`;
  };
  const head = `<thead><tr><th>Check</th><th class="num">First week</th><th class="num">Last 7 days</th><th>State</th></tr></thead>`;
  $("map").innerHTML = head + Object.entries(GROUP_TITLES).map(([g, groupTitle]) => {
    const rs = shown.filter((row) => row.group === g);
    if (!rs.length) return "";
    return `<tbody><tr class="ghead"><th colspan="4">${esc(groupTitle)}</th></tr>` + rs.map((row) => {
      const cell = r.cells[row.id];
      const side = (sd) => (cell[sd] == null ? `<span class="dim">–</span>` : `${value(row, cell, sd)}<small>${count(row, cell, sd)}</small>`);
      return `<tr><td class="lbl">${esc(row.label)}${row.hint ? `<small>${esc(row.hint)}</small>` : ""}</td><td class="num">${side("base")}</td>`
        + `<td class="num">${side("now")}</td><td><span class="flag ${cell.status}">${STATUS[cell.status]}</span></td></tr>`;
    }).join("") + "</tbody>";
  }).join("");
}

function drawTasks(p) {
  const byTask = new Map();
  p.attempts.forEach((a) => { if (!byTask.has(a.task_key)) byTask.set(a.task_key, []); byTask.get(a.task_key).push(a); });
  const keys = [...byTask.keys()].sort((a, b) => title(a).localeCompare(title(b)));
  const latest = (k) => byTask.get(k).reduce((m, a) => (a.date >= m.date ? a : m));
  const failing = keys.filter((k) => latest(k).quality_status === "quality_fail").length;
  $("tasks-sum").textContent = `${keys.length} tasks` + (failing ? `, ${failing} failed last time` : ", none failed last time");
  const mark = (a) => (!graded(a) ? `<i class="l" title="${day(a.date)}: not counted"></i>` : passed(a) ? `<i title="${day(a.date)}: solved"></i>` : `<i class="f" title="${day(a.date)}: failed"></i>`);
  $("tasks").innerHTML = keys.map((k) => {
    const xs = byTask.get(k).sort((a, b) => a.date.localeCompare(b.date)), a = latest(k), g = xs.filter(graded);
    return `<details class="item" id="task-${esc(k)}"><summary><span class="hist" role="img" aria-label="solved ${g.filter(passed).length} of ${g.length}">${xs.map(mark).join("")}</span>`
      + `<span class="t">${esc(title(k))}</span><span class="meta">${esc(k)} · ${esc(a.difficulty)} · ${esc(KIND[a.category] || a.category)} · `
      + `solved ${g.filter(passed).length} of ${g.length}</span></summary>`
      + `<div class="body">${a.quality ? `<p><span class="dim">Latest, ${day(a.date)}:</span> ${esc(a.quality.summary)}</p>` : ""}</div></details>`;
  }).join("");
}

function drawRuns(p) {
  $("runs").innerHTML = `<caption>Every run</caption><thead><tr><th>Date</th><th>Effort</th><th class="num">Solved</th><th class="num">Lost</th><th class="num">Code quality</th></tr></thead><tbody>`
    + [...p.runs].reverse().map((r) => `<tr><td>${day(r.date)}</td><td>${esc(r.effort)}</td><td class="num">${r.passed} of ${r.graded}</td>`
      + `<td class="num">${r.not_graded}</td><td class="num">${r.quality == null ? "–" : r.quality.toFixed(1)}</td></tr>`).join("") + "</tbody>";
}
