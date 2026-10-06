// Renders data.json (written by `python -m runner site`) one model at a time: its pass-rate trend first, then
// its other trends, the checks against its own first week, the tasks where a drop shows, and every task.
import { GROUPS, addDays, evaluate, evidence, graded, mean, passed, rowSpecs, summarize } from "./checks.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (x) => (x == null ? "–" : `${Math.round(x * 100)}%`);
const day = (d, long = false) => new Date(`${d}T00:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: long ? "long" : "short", timeZone: "UTC" });
const span = (a, b) => `${day(a)} – ${day(b)}`;
const COLOR = { opus: "var(--opus)", grok: "var(--grok)" };
const STATUS = {
  worse: "▼ Worse", watch: "◆ Watch", ok: "OK", better: "▲ Better", few: "Too few", early: "Baseline", changed: "Changed", none: "–",
};
const LEGEND = [
  ["worse", "beyond normal noise"], ["watch", "moved the wrong way, could be noise"], ["ok", "no real change"],
  ["better", "improved"], ["early", "baseline week still running"], ["few", "under 3 attempts"],
];
const VERDICT = {
  worse: "▼ Degrading", watch: "◆ Possible degradation", ok: "✓ No degradation", early: "Too early to tell", none: "Not running yet",
};
const WHY = {
  usage_limit: "the plan's usage limit was reached", auth: "sign-in failed", model_mismatch: "a different model was served",
  effort_mismatch: "a different reasoning level was served", web_access: "the agent reached the web",
  isolation_unavailable: "the product was not set up", container_restart: "the machine restarted",
  no_result_event: "the product stopped without a result (often an outage)", timeout: "it ran out of time",
};
const KIND = { bugfix: "bug fix", "long-context": "long context" };
const isShare = (row) => row.kind === "pass" || row.kind === "share" || row.kind === "lost";
const list = (xs) => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`);

let DATA, PRODUCTS, LAST, current;

fetch("data.json", { cache: "no-store" })
  .then((r) => { if (!r.ok) throw new Error(`data.json: HTTP ${r.status}`); return r.json(); })
  .then(init)
  .catch((err) => { $("m-name").textContent = "The latest results could not be loaded"; $("m-via").textContent = err.message; });

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
  const fmt = (d) => new Date(d).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
  $("stamp").textContent = `Updated ${fmt(DATA.generated_at)} UTC` + (DATA.next_run ? ` · next run ${fmt(DATA.next_run)}` : "");

  const tabs = $("tabs");
  tabs.innerHTML = PRODUCTS.map((p) => `<button type="button" role="tab" id="tab-${p.key}" aria-controls="model" data-key="${p.key}">`
    + `<i style="background:${COLOR[p.key]}"></i><b>${esc(p.name)}</b><span>${esc(p.via)}</span></button>`).join("");
  tabs.addEventListener("click", (e) => { const b = e.target.closest("[role=tab]"); if (b) location.hash = b.dataset.key; });
  tabs.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const i = PRODUCTS.indexOf(current), next = PRODUCTS[(i + (e.key === "ArrowRight" ? 1 : PRODUCTS.length - 1)) % PRODUCTS.length];
    location.hash = next.key;
    $(`tab-${next.key}`).focus();
  });
  window.addEventListener("hashchange", route);
  let timer;
  window.addEventListener("resize", () => { clearTimeout(timer); timer = setTimeout(() => current?.attempts.length && drawCharts(current), 150); });
  route();
}

// #opus or #grok picks the model; #opus/TASK-ID opens that task in its list.
function route() {
  const [key, task] = location.hash.slice(1).split("/");
  const p = PRODUCTS.find((x) => x.key === key) || current || PRODUCTS.find((x) => x.attempts.length) || PRODUCTS[0];
  if (p !== current) show(p);
  if (task) {
    const target = document.getElementById(`task-${task}`);
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
  document.documentElement.style.setProperty("--accent", COLOR[p.key]);
  $("m-name").textContent = p.name;
  const live = p.attempts.length > 0;
  document.querySelectorAll(".model > section").forEach((s) => { s.hidden = !live; });
  if (!live) {
    $("m-via").textContent = `${p.via}`;
    $("m-verdict").innerHTML = `<span class="pill none">${VERDICT.none}</span> <span>${esc(p.setup)}</span>`;
    return;
  }
  const rows = rowSpecs(p.attempts, DATA.flags, DATA.dimensions);
  const monitor = (DATA.monitor || []).find((m) => m.series === `${p.model}/${p.effort}/${p.track}`);
  const result = evaluate(rows, p.attempts, LAST, monitor);
  Object.assign(p, { rows, result, summary: summarize(rows, result), evidence: evidence(result), days: dailyStats(p) });
  drawHead(p);
  drawCharts(p);
  drawDaily(p);
  drawMap(p);
  drawEvidence(p);
  drawTasks(p);
  drawRuns(p);
}

// ---- One row per calendar day from the model's first day: that day's results and the trailing 7 days.
function dailyStats(p) {
  const first = p.attempts.map((a) => a.date).sort()[0];
  const out = [];
  for (let d = first; d <= LAST; d = addDays(d, 1)) {
    const today = p.attempts.filter((a) => a.date === d), g = today.filter(graded);
    const week = p.attempts.filter((a) => a.date > addDays(d, -7) && a.date <= d);
    const wg = week.filter(graded);
    const scores = (xs) => xs.filter((a) => a.quality).map((a) => a.quality.overall);
    const tests = (xs) => xs.filter((a) => graded(a) && a.behavior).map((a) => a.behavior.test_runs ?? 0);
    out.push({
      d, n: g.length, k: g.filter(passed).length, total: today.length, lost: today.length - g.length,
      rate: g.length ? g.filter(passed).length / g.length : null,
      roll: wg.length ? wg.filter(passed).length / wg.length : null, rollN: wg.length, rollK: wg.filter(passed).length,
      score: mean(scores(today)), rollScore: mean(scores(week)),
      tests: mean(tests(today)), rollTests: mean(tests(week)),
      lostShare: today.length ? (today.length - g.length) / today.length : null,
      rollLost: week.length ? (week.length - wg.length) / week.length : null,
    });
  }
  return out;
}

// ---- How a check reads
function value(row, cell, side) {
  const v = cell[side];
  if (v == null) return "–";
  return isShare(row) || row.kind === "monitor" ? pct(v) : v.toFixed(1);
}
const change = (row, cell) => (cell.now == null ? value(row, cell, "base") : `${value(row, cell, "base")} → ${value(row, cell, "now")}`);
const phrase = (row, cell) => `${row.label.charAt(0).toLowerCase()}${row.label.slice(1)} (${change(row, cell)})`;
const some = (rs, c, n = 3) => list([...rs.slice(0, n).map((row) => phrase(row, c[row.id])), ...(rs.length > n ? [`${rs.length - n} more below`] : [])]);
function count(row, cell, side) {
  const n = cell[`${side}N`], k = cell[`${side}K`];
  if (n == null) return "";
  if (isShare(row)) return `${k} of ${n}`;
  return `${n} ${row.kind === "quality" ? "scored" : "attempts"}`;
}

function drawHead(p) {
  const { summary: s, result: r } = p, c = r.cells;
  $("m-via").textContent = `${p.via} · ${p.effort} effort · tracked since ${day(r.first, true)}`;
  let why;
  if (s.level === "early") {
    why = `The baseline week runs ${span(r.first, r.baseEnd)}. The first verdict comes on ${day(addDays(r.baseEnd, 1), true)}.`;
  } else if (s.level === "worse") {
    why = `Clearly worse than its first week on ${some(s.rows, c)}.` + (s.watch.length ? ` ${s.watch.length} more to watch.` : "");
  } else if (s.level === "watch") {
    why = `Moved the wrong way, but could still be noise: ${some(s.rows, c)}.`;
  } else {
    why = `${s.tested} checks over ${span(r.nowStart, r.last)} against its first week. Nothing moved beyond normal noise.`
      + (s.better.length ? ` Better on ${some(s.better, c)}.` : "");
  }
  $("m-verdict").innerHTML = `<span class="pill ${s.level}">${VERDICT[s.level]}</span> <span>${why}</span>`;
}

function drawCharts(p) {
  const days = p.days, last = days[days.length - 1], r = p.result;
  const baseIdx = [0, days.findIndex((x) => x.d > r.baseEnd) - 1].map((i) => (i < 0 ? days.length - 1 : i));
  const band = { i0: baseIdx[0], i1: baseIdx[1], label: "Baseline week" };
  const base = r.cells.all.base;
  $("hero").innerHTML = `${pct(last.roll)}<small>last 7 days</small>`;
  $("trend-sub").textContent = `${last.rollK} of ${last.rollN} tasks passed in the last 7 days`
    + (base == null ? "" : ` · baseline week ${pct(base)}${r.building ? " so far" : ""}`);
  const rates = days.flatMap((x) => [x.rate, x.roll]).filter((v) => v != null);
  const lo = Math.min(0.5, Math.floor(Math.min(...rates) * 4) / 4);
  const ticks = [];
  for (let t = lo; t <= 1.0001; t += 0.25) ticks.push(t);
  lineChart($("chart"), {
    days, color: COLOR[p.key], domain: [lo, 1], ticks, fmt: pct, band, height: [240, 300],
    dot: (x) => x.rate, line: (x) => x.roll,
    ref: base == null ? null : { v: base, label: `Baseline ${pct(base)}` },
    aria: `${p.name} pass rate by day, with a 7-day rolling line`,
    tip: (x) => `<b>${day(x.d, true)}</b><br>${x.n ? `${x.k} of ${x.n} passed that day` : "no graded tasks that day"}`
      + `<br>Last 7 days: ${pct(x.roll)} (${x.rollK} of ${x.rollN})` + (x.lost ? `<br>${x.lost} lost to outages or limits` : ""),
  });
  $("chart-note").textContent = "Dots are single days; each day runs a different slice of the tasks, so they move with the mix. "
    + `The line is the 7-day rolling pass rate. The shaded area is the baseline week. The axis starts at ${pct(lo)}.`;

  const metrics = [
    { id: "q:overall", title: "Reviewer score", sub: "1 to 5, higher is better", dot: (x) => x.score, line: (x) => x.rollScore,
      fmt: (v) => v.toFixed(1), domain: (vs) => [Math.min(3, Math.floor(Math.min(...vs) * 2) / 2), 5] },
    { id: "h:tests", title: "Test runs per attempt", sub: "fewer means less checking", dot: (x) => x.tests, line: (x) => x.rollTests,
      fmt: (v) => v.toFixed(1), domain: (vs) => [0, Math.max(4, Math.ceil(Math.max(...vs)))] },
    { id: "s:lost", title: "Attempts lost", sub: "outages, usage limits, wrong model", dot: (x) => x.lostShare, line: (x) => x.rollLost,
      fmt: pct, domain: (vs) => [0, Math.max(0.2, Math.ceil(Math.max(...vs) * 10) / 10)] },
  ];
  $("minis").innerHTML = metrics.map((m) => {
    const cell = r.cells[m.id] || { status: "none" };
    const now = m.line(last);
    return `<article class="mini"><h3>${m.title}</h3><p class="msub">${m.sub}</p>`
      + `<p class="mval">${now == null ? "–" : m.fmt(now)}<small>last 7 days</small></p>`
      + `<p class="mbase">${cell.base == null ? "" : `Baseline ${m.fmt(cell.base)} `}<span class="st ${cell.status}">${STATUS[cell.status]}</span></p>`
      + `<div class="mchart" id="mini-${m.id.replace(":", "-")}"></div></article>`;
  }).join("");
  metrics.forEach((m) => {
    const vs = days.flatMap((x) => [m.dot(x), m.line(x)]).filter((v) => v != null);
    if (!vs.length) { $(`mini-${m.id.replace(":", "-")}`).innerHTML = `<p class="dim">No data yet.</p>`; return; }
    const [lo2, hi2] = m.domain(vs);
    const cell = r.cells[m.id] || {};
    lineChart($(`mini-${m.id.replace(":", "-")}`), {
      days, color: COLOR[p.key], domain: [lo2, hi2], ticks: [lo2, hi2], fmt: m.fmt, band, height: [120, 130], mini: true,
      dot: m.dot, line: m.line, ref: cell.base == null ? null : { v: cell.base, label: "" },
      aria: `${p.name} ${m.title.toLowerCase()} by day`,
      tip: (x) => `<b>${day(x.d, true)}</b><br>That day: ${m.dot(x) == null ? "–" : m.fmt(m.dot(x))}<br>Last 7 days: ${m.line(x) == null ? "–" : m.fmt(m.line(x))}`,
    });
  });
}

// One series over calendar days: faint dots for single days, a 2px line for the 7-day rolling value, the
// baseline week shaded and its level as a reference line. Values and labels stay in text colours.
function lineChart(host, cfg) {
  const { days } = cfg;
  const W = Math.max(260, Math.round(host.clientWidth || 600)), narrow = W < 520;
  const H = narrow ? cfg.height[0] : cfg.height[1];
  const L = cfg.mini ? 34 : 42, R = cfg.mini ? 40 : 52, T = cfg.mini ? 8 : 22, B = 24;
  const n = days.length, step = n > 1 ? (W - L - R) / (n - 1) : 0;
  const x = (i) => (n === 1 ? L + (W - L - R) / 2 : L + i * step);
  const [lo, hi] = cfg.domain;
  const y = (v) => T + (1 - (Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo || 1)) * (H - T - B);
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(cfg.aria)}">`;
  if (cfg.band) {
    const x0 = Math.max(L, x(cfg.band.i0) - step / 2), x1 = Math.min(W - R, x(cfg.band.i1) + step / 2);
    svg += `<rect class="band" x="${x0}" y="${T}" width="${Math.max(2, x1 - x0)}" height="${H - T - B}"/>`;
    if (!cfg.mini) svg += `<text class="band-label" x="${x0 + 6}" y="${T - 7}">${esc(cfg.band.label)}</text>`;
  }
  cfg.ticks.forEach((t) => {
    svg += `<line class="grid-line" x1="${L}" x2="${W - R}" y1="${y(t)}" y2="${y(t)}"/>`;
    svg += `<text class="tick" x="${L - 8}" y="${y(t) + 4}" text-anchor="end">${cfg.fmt(t)}</text>`;
  });
  if (cfg.ref) {
    svg += `<line class="ref-line" x1="${L}" x2="${W - R}" y1="${y(cfg.ref.v)}" y2="${y(cfg.ref.v)}"/>`;
    // Labelled just right of the baseline week and below the line, where the high daily dots are not.
    const lx = cfg.band ? x(cfg.band.i1) + step / 2 + 6 : L + 6;
    if (cfg.ref.label && lx < W - R - 90) {
      svg += `<text class="ref-label" x="${lx}" y="${y(cfg.ref.v) + 15}">${esc(cfg.ref.label)}</text>`;
    }
  }
  const every = Math.max(1, Math.ceil(n / (cfg.mini ? 3 : narrow ? 4 : 8)));
  days.forEach((d, i) => {
    if (i % every === 0 || i === n - 1) svg += `<text class="tick" x="${x(i)}" y="${H - 6}" text-anchor="middle">${day(d.d)}</text>`;
  });
  days.forEach((d, i) => {
    const v = cfg.dot(d);
    if (v != null) svg += `<circle class="dot" cx="${x(i)}" cy="${y(v)}" r="${cfg.mini ? 3 : 4}" fill="${cfg.color}"/>`;
  });
  let path = "", pen = false, end = null;
  days.forEach((d, i) => {
    const v = cfg.line(d);
    if (v == null) { pen = false; return; }
    path += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
    pen = true;
    end = { i, v };
  });
  svg += `<path d="${path}" fill="none" stroke="${cfg.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
  if (end) {
    svg += `<circle class="end" cx="${x(end.i)}" cy="${y(end.v)}" r="4.5" fill="${cfg.color}"/>`;
    svg += `<text class="end-label" x="${x(end.i) + 9}" y="${y(end.v) + 4}">${cfg.fmt(end.v)}</text>`;
  }
  svg += `<line class="cursor" x1="0" x2="0" y1="${T}" y2="${H - B}" visibility="hidden"/>`;
  svg += `<rect class="hit" x="${L - 12}" y="0" width="${W - L - R + 24}" height="${H}" fill="transparent"/></svg>`;
  host.innerHTML = svg;

  const el = host.querySelector("svg"), cursor = host.querySelector(".cursor"), tip = $("tooltip");
  const showTip = (evt) => {
    const box = el.getBoundingClientRect(), px = ((evt.clientX - box.left) / box.width) * W;
    const i = Math.max(0, Math.min(n - 1, Math.round(n === 1 ? 0 : (px - L) / step)));
    cursor.setAttribute("x1", x(i)); cursor.setAttribute("x2", x(i)); cursor.setAttribute("visibility", "visible");
    tip.innerHTML = cfg.tip(days[i]);
    tip.hidden = false;
    tip.style.left = `${Math.max(8, Math.min(evt.clientX + 14, window.innerWidth - tip.offsetWidth - 8))}px`;
    tip.style.top = `${evt.clientY + 14}px`;
  };
  const hit = host.querySelector(".hit");
  hit.addEventListener("pointermove", showTip);
  hit.addEventListener("pointerdown", showTip);
  hit.addEventListener("pointerleave", () => { cursor.setAttribute("visibility", "hidden"); tip.hidden = true; });
}

function drawDaily(p) {
  $("daily").innerHTML = `<thead><tr><th>Day</th><th class="num">Passed</th><th class="num">Last 7 days</th><th class="num">Reviewer score</th><th class="num">Lost</th></tr></thead><tbody>`
    + [...p.days].reverse().map((x) => `<tr><td>${day(x.d)}</td><td class="num">${x.n ? `${x.k} of ${x.n}` : "–"}</td>`
      + `<td class="num">${pct(x.roll)}</td><td class="num">${x.score == null ? "–" : x.score.toFixed(1)}</td><td class="num">${x.lost || "–"}</td></tr>`).join("")
    + "</tbody>";
}

function drawMap(p) {
  const r = p.result;
  $("map-note").textContent = r.building
    ? `The baseline week is still running, so each check shows the baseline so far. From ${day(addDays(r.baseEnd, 1), true)} it is compared with the last 7 days.`
    : `Baseline week ${span(r.first, r.baseEnd)} against the last 7 days, ${span(r.nowStart, r.last)}.`;
  $("legend").innerHTML = LEGEND.map(([s, text]) => `<li><span class="st ${s}">${STATUS[s]}</span> ${text}</li>`).join("");
  const shown = p.rows.filter((row) => r.cells[row.id]?.status !== "none");
  const head = `<thead><tr><th>Check</th><th class="num">Baseline week</th><th class="num">Last 7 days</th><th>Status</th></tr></thead>`;
  const groups = Object.entries(GROUPS).map(([g, title]) => {
    const rs = shown.filter((row) => row.group === g);
    if (!rs.length) return "";
    const flagged = rs.filter((row) => ["worse", "watch"].includes(r.cells[row.id].status)).length;
    // The kinds of task are many and usually quiet: folded unless one of them moved.
    const folded = g === "kinds" && !flagged;
    const tag = flagged ? `<em class="st watch">${flagged} to look at</em>` : `<em class="dim">${rs.length} checks</em>`;
    return `<tbody class="${folded ? "folded" : ""}"><tr class="ghead"><th colspan="4">`
      + `<button type="button" aria-expanded="${!folded}"><span class="tw"></span>${esc(title)} ${tag}</button></th></tr>`
      + rs.map((row) => {
        const cell = r.cells[row.id];
        const side = (s) => (cell[s] == null ? `<span class="dim">–</span>` : `${value(row, cell, s)}<small>${count(row, cell, s)}</small>`);
        const hint = row.kind === "monitor" && cell.message && cell.status !== "early" ? cell.message : row.hint;
        return `<tr><td class="lbl">${esc(row.label)}${hint ? `<small>${esc(hint)}</small>` : ""}</td>`
          + `<td class="num">${side("base")}</td><td class="num">${side("now")}</td>`
          + `<td><span class="st ${cell.status}">${STATUS[cell.status]}</span></td></tr>`;
      }).join("") + `</tbody>`;
  }).join("");
  $("map").innerHTML = head + groups;
  $("map").querySelectorAll(".ghead button").forEach((b) => b.addEventListener("click", () => {
    const folded = b.closest("tbody").classList.toggle("folded");
    b.setAttribute("aria-expanded", String(!folded));
  }));
}

function drawEvidence(p) {
  const r = p.result, items = p.evidence, SHOW = 6;
  $("ev-note").textContent = r.building
    ? "No comparison yet, so these are the failures during the baseline week."
    : "Tasks that failed in the last 7 days, and passes whose reviewer score fell a full point below the same task's baseline.";
  if (!items.length) {
    $("evidence").innerHTML = `<li class="empty">${r.building ? "No task has failed so far." : "Nothing: no task failed in the last 7 days, and no reviewer score fell a full point."}</li>`;
    return;
  }
  $("evidence").innerHTML = items.map(({ kind, attempt: a, before, from, to }, i) => {
    const what = kind === "fail" ? (a.error_code === "timeout" ? "Timed out on" : "Failed") : "Passed, but scored lower on";
    const record = before ? (kind === "fail" ? `In its baseline week this task passed ${before.k} of ${before.n}.` : `Reviewer score ${from.toFixed(1)} → ${to.toFixed(1)}.`)
      : r.building ? "" : "This task did not run in the baseline week.";
    const notes = [a.quality?.summary, a.behavior?.flags?.length ? `Flagged: ${a.behavior.flags.map((f) => DATA.flags[f] || f).join("; ")}.` : null].filter(Boolean);
    return `<li class="ev-${kind}"${i >= SHOW ? " hidden" : ""}><p class="what"><span class="mark ${kind}"></span>${what} `
      + `<a href="#${p.key}/${encodeURIComponent(a.task_key)}">${esc(a.task_key)}</a> <span class="dim">· ${day(a.date)} · ${esc(a.difficulty)} · ${esc(KIND[a.category] || a.category)}</span></p>`
      + (record ? `<p class="rec">${record}</p>` : "") + notes.map((t) => `<p class="say">${esc(t)}</p>`).join("") + `</li>`;
  }).join("") + (items.length > SHOW ? `<li class="all"><button type="button">Show all ${items.length}</button></li>` : "");
  $("evidence").querySelector(".all button")?.addEventListener("click", (e) => {
    $("evidence").querySelectorAll("li[hidden]").forEach((li) => { li.hidden = false; });
    e.target.closest("li").remove();
  });
}

function drawTasks(p) {
  const byTask = new Map();
  p.attempts.forEach((a) => { if (!byTask.has(a.task_key)) byTask.set(a.task_key, []); byTask.get(a.task_key).push(a); });
  const order = { hard: 0, medium: 1, easy: 2 };
  const keys = [...byTask.keys()].sort((a, b) => (order[byTask.get(a)[0].difficulty] ?? 1) - (order[byTask.get(b)[0].difficulty] ?? 1) || a.localeCompare(b));
  const latest = (k) => byTask.get(k).reduce((m, a) => (a.date >= m.date ? a : m));
  const failing = keys.filter((k) => latest(k).quality_status === "quality_fail").length;
  $("tasks-sum").textContent = `${keys.length} tasks` + (failing ? `, ${failing} failed last time` : ", none failing last time");
  const mark = (a) => (a.quality_status === "quality_pass" ? `<span class="pass" title="passed">✓</span>`
    : a.quality_status === "quality_fail" ? `<span class="fail" title="failed">✗</span>` : `<span class="skip" title="not graded">–</span>`);
  $("tasks").innerHTML = `<div class="trow"><span>Task</span><span>Latest</span><span>Record</span></div>` + keys.map((k) => {
    const xs = byTask.get(k).sort((a, b) => a.date.localeCompare(b.date)), a = latest(k), g = xs.filter(graded);
    const label = a.quality_status === "quality_pass" ? `<span class="pass">✓ Passed</span>`
      : a.quality_status === "quality_fail" ? `<span class="fail">✗ ${a.error_code === "timeout" ? "Timed out" : "Failed"}</span>` : `<span class="skip">Not graded</span>`;
    const why = graded(a) ? "" : `<p>Not graded: ${esc(WHY[a.error_code] || a.error_code || "unknown error")}.</p>`;
    const flags = a.behavior?.flags?.length ? `<p>Flagged: ${a.behavior.flags.map((f) => esc(DATA.flags[f] || f)).join("; ")}.</p>` : "";
    return `<details id="task-${esc(k)}"><summary><span class="tc"><span class="tn">${esc(k)}</span>`
      + `<span class="tk">${esc(a.difficulty)} · ${esc(KIND[a.category] || a.category)} · ${a.source === "mined" ? "real bug fix" : "benchmark task"}</span></span>`
      + `<span class="res">${label}<small>${day(a.date)}${a.quality ? ` · quality ${a.quality.overall.toFixed(1)}` : ""}</small></span>`
      + `<span class="rec">${g.filter(passed).length} of ${g.length} passed<small class="hist">${xs.map(mark).join("")}</small></span></summary>`
      + `<div class="notes">${why}${a.quality ? `<p>${esc(a.quality.summary)}</p>` : ""}${flags}</div></details>`;
  }).join("");
}

function drawRuns(p) {
  $("runs").innerHTML = `<caption>Every run</caption><thead><tr><th>Date</th><th>Effort</th><th class="num">Passed</th><th class="num">Not graded</th><th class="num">Quality</th></tr></thead><tbody>`
    + [...p.runs].reverse().map((r) => `<tr><td>${day(r.date)}</td><td>${esc(r.effort)}</td><td class="num">${r.passed} of ${r.graded}</td>`
      + `<td class="num">${r.not_graded}</td><td class="num">${r.quality == null ? "–" : r.quality.toFixed(2)}</td></tr>`).join("") + "</tbody>";
}
