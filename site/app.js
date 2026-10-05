// Renders data.json (written by `python -m runner site`): a verdict per product, the map of every check
// against its baseline, the tasks where a drop shows, one chart and folded details.
import { GROUPS, addDays, evaluate, evidence, graded, passed, rowSpecs, summarize } from "./checks.js";

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
const WHY = {
  usage_limit: "the plan's usage limit was reached", auth: "sign-in failed", model_mismatch: "a different model was served",
  effort_mismatch: "a different reasoning level was served", web_access: "the agent reached the web",
  isolation_unavailable: "the product was not set up", container_restart: "the machine restarted",
  no_result_event: "the product stopped without a result (often an outage)", timeout: "it ran out of time",
};
const KIND = { bugfix: "bug fix", "long-context": "long context" };
const anchor = (key) => `task-${key.replace(/[^A-Za-z0-9_-]/g, "-")}`;
const isShare = (row) => row.kind === "pass" || row.kind === "share" || row.kind === "lost";

fetch("data.json", { cache: "no-store" })
  .then((r) => { if (!r.ok) throw new Error(`data.json: HTTP ${r.status}`); return r.json(); })
  .then(render)
  .catch((err) => { $("verdicts").innerHTML = `<p class="dim">The latest results could not be loaded (${esc(err.message)}).</p>`; });

function render(DATA) {
  // One series per product: the effort of its newest run. Runs at other efforts stay in the run log.
  const products = DATA.products.map((p) => {
    const runs = DATA.runs.filter((r) => r.track === p.track);
    const effort = runs.length ? runs[runs.length - 1].effort : null;
    const attempts = DATA.attempts.filter((a) => a.track === p.track && a.effort === effort);
    return { ...p, key: p.track.startsWith("cursor") ? "grok" : "opus", effort, attempts };
  });
  const dates = [...new Set(products.flatMap((p) => p.attempts.map((a) => a.date)))].sort();
  const last = dates[dates.length - 1];
  const fmt = (d) => new Date(d).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
  $("stamp").textContent = `Updated ${fmt(DATA.generated_at)} UTC` + (DATA.next_run ? ` · next run ${fmt(DATA.next_run)}` : "");

  const rows = rowSpecs(products.flatMap((p) => p.attempts), DATA.flags, DATA.dimensions);
  products.forEach((p) => {
    const monitor = (DATA.monitor || []).find((m) => m.series === `${p.model}/${p.effort}/${p.track}`);
    p.result = last ? evaluate(rows, p.attempts, last, monitor) : null;
    p.summary = summarize(rows, p.result);
    p.evidence = evidence(p.result);
  });

  drawVerdicts(products, rows);
  drawMap(products, rows);
  drawEvidence(DATA, products);
  const live = products.filter((p) => p.attempts.length);
  drawChart(live, dates);
  let resizeTimer;
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => drawChart(live, dates), 150); });
  drawTasks(DATA, products);
  drawRuns(DATA, products);
  openFromHash();
  window.addEventListener("hashchange", openFromHash);
}

// ---- How a cell reads, by kind of check
function value(row, cell, side) {
  const v = cell[side];
  if (v == null) return "–";
  return isShare(row) || row.kind === "monitor" ? pct(v) : v.toFixed(1);
}
const change = (row, cell) => (cell.now == null ? value(row, cell, "base") : `${value(row, cell, "base")} → ${value(row, cell, "now")}`);
function counts(row, cell) {
  if (row.kind === "monitor") return cell.message && cell.status !== "early" ? esc(cell.message) : "";
  if (cell.baseN == null) return "";
  const side = (k, n) => (isShare(row) ? `${k} of ${n}` : `${n}`);
  const what = row.kind === "quality" ? " scored" : row.kind === "work" ? " attempts" : "";
  return (cell.nowN == null ? side(cell.baseK, cell.baseN) : `${side(cell.baseK, cell.baseN)} → ${side(cell.nowK, cell.nowN)}`) + what;
}
const phrase = (row, cell) => `${row.label.charAt(0).toLowerCase()}${row.label.slice(1)} (${change(row, cell)})`;
const list = (xs) => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`);
// The first few rows by name, the rest as a count: the map below has them all.
const some = (rs, c, n = 3) => list([...rs.slice(0, n).map((row) => phrase(row, c[row.id])), ...(rs.length > n ? [`${rs.length - n} more below`] : [])]);

function drawVerdicts(products, rows) {
  const byId = Object.fromEntries(rows.map((r) => [r.id, r]));
  $("verdicts").innerHTML = products.map((p) => {
    const head = `<div class="vhead"><i style="background:${COLOR[p.key]}"></i><b>${esc(p.name)}</b><span>${esc(p.via)}${p.effort ? ` · ${esc(p.effort)} effort` : ""}</span></div>`;
    const s = p.summary, r = p.result;
    if (s.level === "none") return `<article class="verdict none">${head}<p class="word">Not running yet</p><p class="why">${esc(p.setup)}</p></article>`;
    const c = r.cells;
    const figures = [`Pass rate ${change(byId.all, c.all)}`, c["q:overall"]?.base != null ? `score ${change(byId["q:overall"], c["q:overall"])}` : null,
      c["s:lost"]?.nowK ? `${c["s:lost"].nowK} lost` : null].filter(Boolean).join(" · ");
    let word, why;
    if (s.level === "early") {
      word = "Too early to tell";
      why = `Its baseline week runs ${span(r.first, r.baseEnd)}. The first verdict comes on ${day(addDays(r.baseEnd, 1), true)}. `
        + `So far ${c.all.baseK} of ${c.all.baseN} tasks passed.`;
    } else if (s.level === "worse") {
      word = "Degrading";
      why = `Clearly worse than its first week on ${some(s.rows, c)}.`
        + (s.watch.length ? ` ${s.watch.length} more to watch.` : "");
    } else if (s.level === "watch") {
      word = "Possible degradation";
      why = `Moved the wrong way, but could still be noise: ${some(s.rows, c)}. A verdict needs a bigger or longer drop.`;
    } else {
      word = "No degradation";
      why = `${s.tested} checks, ${span(r.nowStart, r.last)} against its first week (${span(r.first, r.baseEnd)}). Nothing moved beyond normal noise.`
        + (s.better.length ? ` Better on ${some(s.better, c)}.` : "");
    }
    return `<article class="verdict ${s.level}">${head}<p class="word">${word}</p><p class="why">${why}</p><p class="figs">${figures}</p></article>`;
  }).join("");
}

function drawMap(products, rows) {
  const live = products.filter((p) => p.result);
  const ref = live[0]?.result;
  $("map-note").textContent = !ref ? "No results yet."
    : live.every((p) => p.result.building) ? `Each cell shows the baseline so far. From ${day(addDays(ref.baseEnd, 1), true)} it compares the last 7 days with the baseline week.`
      : "Each cell: baseline week → last 7 days, with the attempts behind each side.";
  $("legend").innerHTML = LEGEND.map(([s, text]) => `<li><span class="st ${s}">${STATUS[s]}</span> ${text}</li>`).join("");
  const shown = rows.filter((r) => live.some((p) => p.result.cells[r.id]?.status !== "none"));
  const head = `<thead><tr><th>Check</th>${live.map((p) => `<th><span class="sw" style="background:${COLOR[p.key]}"></span>${esc(p.name)}</th>`).join("")}</tr></thead>`;
  const groups = Object.entries(GROUPS).map(([g, title]) => {
    const rs = shown.filter((r) => r.group === g);
    if (!rs.length) return "";
    const flagged = rs.filter((r) => live.some((p) => ["worse", "watch"].includes(p.result.cells[r.id]?.status))).length;
    // The kinds of task are many and usually quiet: folded unless one of them moved.
    const folded = g === "kinds" && !flagged;
    const tag = flagged ? `<em class="st watch">${flagged} to look at</em>` : `<em class="dim">${rs.length} checks</em>`;
    return `<tbody class="${folded ? "folded" : ""}"><tr class="ghead"><th colspan="${live.length + 1}">`
      + `<button type="button" aria-expanded="${!folded}"><span class="tw"></span>${esc(title)} ${tag}</button></th></tr>`
      + rs.map((r) => `<tr><td class="lbl">${esc(r.label)}${r.hint ? `<small>${esc(r.hint)}</small>` : ""}</td>`
        + live.map((p) => {
          const cell = p.result.cells[r.id] || { status: "none" };
          const shownValue = cell.status !== "none" && !(r.kind === "monitor" && cell.base == null);
          return `<td class="cell"><span class="st ${cell.status}">${STATUS[cell.status]}</span>`
            + (shownValue ? `<span class="v">${change(r, cell)}</span>` : "") + `<small>${counts(r, cell)}</small></td>`;
        }).join("") + `</tr>`).join("") + `</tbody>`;
  }).join("");
  $("map").innerHTML = head + groups;
  $("map").querySelectorAll(".ghead button").forEach((b) => b.addEventListener("click", () => {
    const folded = b.closest("tbody").classList.toggle("folded");
    b.setAttribute("aria-expanded", String(!folded));
  }));
}

function drawEvidence(DATA, products) {
  const live = products.filter((p) => p.result);
  const building = live.length > 0 && live.every((p) => p.result.building);
  const items = live.flatMap((p) => p.evidence.map((e) => ({ ...e, p }))).sort((x, y) => y.attempt.date.localeCompare(x.attempt.date));
  $("ev-note").textContent = building
    ? "No comparison yet, so these are the failures during the baseline week."
    : "Tasks that failed in the last 7 days, and passes whose reviewer score fell a full point below the same task's baseline.";
  if (!items.length) {
    $("evidence").innerHTML = `<li class="empty">${building ? "No task has failed so far." : "Nothing: no task failed in the last 7 days, and no reviewer score fell a full point."}</li>`;
    return;
  }
  const SHOW = 6;
  $("evidence").innerHTML = items.map(({ kind, attempt: a, before, from, to, p }, i) => {
    const what = kind === "fail" ? (a.error_code === "timeout" ? "timed out on" : "failed") : "passed, but scored lower on";
    const record = before ? (kind === "fail" ? `In its baseline week this task passed ${before.k} of ${before.n}.` : `Reviewer score ${from.toFixed(1)} → ${to.toFixed(1)}.`)
      : p.result.building ? "" : "This task did not run in the baseline week.";
    const notes = [a.quality?.summary, a.behavior?.flags?.length ? `Flagged: ${a.behavior.flags.map((f) => DATA.flags[f] || f).join("; ")}.` : null].filter(Boolean);
    return `<li class="ev-${kind}"${i >= SHOW ? " hidden" : ""}><p class="what"><span class="sw" style="background:${COLOR[p.key]}"></span><b>${esc(p.name)}</b> ${what} `
      + `<a href="#${anchor(a.task_key)}">${esc(a.task_key)}</a> <span class="dim">· ${day(a.date)} · ${esc(a.difficulty)} · ${esc(KIND[a.category] || a.category)}</span></p>`
      + (record ? `<p class="rec">${record}</p>` : "") + notes.map((n) => `<p class="say">${esc(n)}</p>`).join("") + `</li>`;
  }).join("") + (items.length > SHOW ? `<li class="all"><button type="button">Show all ${items.length}</button></li>` : "");
  $("evidence").querySelector(".all button")?.addEventListener("click", (e) => {
    $("evidence").querySelectorAll("li[hidden]").forEach((li) => { li.hidden = false; });
    e.target.closest("li").remove();
  });
}

function drawChart(live, dates) {
  const host = $("chart");
  if (!dates.length) { host.innerHTML = `<p class="dim">Nothing to chart yet.</p>`; return; }
  const series = live.map((p) => ({ p, pts: dates.map((d) => {
    const g = p.attempts.filter((a) => a.date === d && graded(a)), k = g.filter(passed).length;
    return { d, n: g.length, k, v: g.length ? k / g.length : null };
  }) }));
  const vals = series.flatMap((s) => s.pts.map((pt) => pt.v)).filter((v) => v != null);
  const lo = Math.min(0.5, Math.floor(Math.min(...vals) * 4) / 4);
  // Draw at the container's own width, so labels keep their size on a phone.
  const W = Math.max(320, Math.round(host.clientWidth || 860)), narrow = W < 560;
  const H = narrow ? 236 : 296, L = 40, R = narrow ? 104 : 150, T = 12, B = 32 + 8 * series.length;
  const x = (i) => (dates.length === 1 ? L + (W - L - R) / 2 : L + (i * (W - L - R)) / (dates.length - 1));
  const y = (v) => T + (1 - (v - lo) / (1 - lo)) * (H - T - B);
  const ticks = [];
  for (let t = lo; t <= 1.0001; t += 0.25) ticks.push(t);
  const every = Math.max(1, Math.ceil(dates.length / (narrow ? 4 : 10)));
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Daily pass rate for ${live.map((p) => p.name).join(" and ")}">`;
  ticks.forEach((t) => {
    svg += `<line class="${t === lo ? "base-line" : "grid-line"}" x1="${L}" x2="${W - R}" y1="${y(t)}" y2="${y(t)}"/>`;
    svg += `<text x="${L - 8}" y="${y(t) + 4}" text-anchor="end">${Math.round(t * 100)}%</text>`;
  });
  // Each product's baseline week, as a bar under the axis in its colour.
  const step = dates.length > 1 ? (W - L - R) / (dates.length - 1) : W - L - R;
  series.forEach(({ p }, si) => {
    const r = p.result;
    if (!r) return;
    const i0 = dates.indexOf(r.first), i1 = dates.filter((d) => d <= r.baseEnd).length - 1;
    if (i0 < 0 || i1 < i0) return;
    const yb = H - B + 6 + si * 8, x0 = Math.max(L - 6, x(i0) - step / 2), x1 = Math.min(W - R + 6, x(i1) + step / 2);
    svg += `<rect x="${x0}" y="${yb}" width="${Math.max(4, x1 - x0)}" height="4" rx="2" fill="${COLOR[p.key]}" opacity="0.6"/>`;
  });
  dates.forEach((d, i) => { if (i % every === 0 || i === dates.length - 1) svg += `<text x="${x(i)}" y="${H - 6}" text-anchor="middle">${day(d)}</text>`; });
  const ends = [];
  // Where both products score the same, the lines overlap: the first is drawn wide underneath and the
  // second thin on top, so both stay visible.
  series.forEach(({ p, pts }, si) => {
    const width = si === 0 && series.length > 1 ? 6 : 2.5, r = si === 0 && series.length > 1 ? 6.5 : 4;
    let path = "", pen = false;
    pts.forEach((pt, i) => { if (pt.v == null) { pen = false; return; } path += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(pt.v).toFixed(1)}`; pen = true; });
    svg += `<path d="${path}" fill="none" stroke="${COLOR[p.key]}" stroke-width="${width}" stroke-linejoin="round" stroke-linecap="round"/>`;
    pts.forEach((pt, i) => { if (pt.v != null) svg += `<circle cx="${x(i)}" cy="${y(pt.v)}" r="${r}" fill="${COLOR[p.key]}" stroke="var(--paper)" stroke-width="2"/>`; });
    const lastPt = [...pts].reverse().find((pt) => pt.v != null);
    if (lastPt) ends.push({ p, y: y(lastPt.v), v: lastPt.v });
  });
  // Direct labels at the right edge, nudged apart so they never overlap.
  ends.sort((a, b) => a.y - b.y);
  for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 20) ends[i].y = ends[i - 1].y + 20;
  ends.forEach((e) => { svg += `<text class="end" x="${W - R + 14}" y="${e.y + 5}" style="fill:${COLOR[e.p.key]}">${esc(e.p.name)} ${pct(e.v)}</text>`; });
  svg += `<line class="cursor" id="cursor" x1="0" x2="0" y1="${T}" y2="${H - B}" visibility="hidden"/>`;
  svg += `<rect id="hit" x="${L - 20}" y="${T}" width="${W - L - R + 40}" height="${H - T - B}" fill="transparent"/></svg>`;
  host.innerHTML = svg;
  $("chart-note").textContent = `The bars above the dates mark each product's baseline week. Each day runs a different slice of the tasks, so single days move with the mix; the checks above adjust for that. The axis starts at ${Math.round(lo * 100)}%.`;

  const svgEl = host.querySelector("svg"), hit = $("hit"), cursor = $("cursor"), tip = $("tooltip");
  const show = (evt) => {
    const box = svgEl.getBoundingClientRect(), px = ((evt.clientX - box.left) / box.width) * W;
    let i = 0, best = Infinity;
    dates.forEach((_, j) => { const dist = Math.abs(x(j) - px); if (dist < best) { best = dist; i = j; } });
    cursor.setAttribute("x1", x(i)); cursor.setAttribute("x2", x(i)); cursor.setAttribute("visibility", "visible");
    tip.innerHTML = `<b>${day(dates[i], true)}</b><br>` + series.map(({ p, pts }) => {
      const pt = pts[i];
      return `<span class="sw" style="background:${COLOR[p.key]}"></span>${esc(p.name)}: ${pt.v == null ? "no graded tasks" : `${pt.k} of ${pt.n} passed`}`;
    }).join("<br>");
    tip.hidden = false;
    tip.style.left = `${Math.max(8, Math.min(evt.clientX + 14, window.innerWidth - tip.offsetWidth - 8))}px`;
    tip.style.top = `${evt.clientY + 14}px`;
  };
  hit.addEventListener("pointermove", show);
  hit.addEventListener("pointerdown", show);
  hit.addEventListener("pointerleave", () => { cursor.setAttribute("visibility", "hidden"); tip.hidden = true; });
}

function drawTasks(DATA, products) {
  const byTask = (p, f) => {
    const m = new Map();
    p.attempts.forEach((a) => f(m, a));
    return [p.key, m];
  };
  const latest = Object.fromEntries(products.map((p) => byTask(p, (m, a) => { const o = m.get(a.task_key); if (!o || a.date >= o.date) m.set(a.task_key, a); })));
  const record = Object.fromEntries(products.map((p) => byTask(p, (m, a) => {
    if (!graded(a)) return;
    const r = m.get(a.task_key) || { k: 0, n: 0 }; r.n += 1; r.k += passed(a) ? 1 : 0; m.set(a.task_key, r);
  })));
  const meta = new Map(products.flatMap((p) => p.attempts).map((a) => [a.task_key, a]));
  const order = { hard: 0, medium: 1, easy: 2 };
  const keys = [...meta.keys()].sort((a, b) => (order[meta.get(a).difficulty] ?? 1) - (order[meta.get(b).difficulty] ?? 1) || a.localeCompare(b));
  const failing = keys.filter((k) => products.some((p) => latest[p.key].get(k)?.quality_status === "quality_fail")).length;
  $("tasks-sum").textContent = `${keys.length} tasks` + (failing ? `, ${failing} failed last time` : ", none failing last time");
  const res = (p, k) => {
    const a = latest[p.key].get(k);
    if (!a) return `<span class="dim">Not run</span>`;
    const r = record[p.key].get(k);
    const bits = [day(a.date), r ? `${r.k} of ${r.n} passed so far` : null, a.quality ? `quality ${a.quality.overall.toFixed(1)}` : null].filter(Boolean).join(" · ");
    const label = a.quality_status === "quality_pass" ? `<span class="pass">✓ Passed</span>`
      : a.quality_status === "quality_fail" ? `<span class="fail">✗ ${a.error_code === "timeout" ? "Timed out" : "Failed"}</span>`
        : `<span class="skip">Not graded</span>`;
    return `${label}<small>${bits}</small>`;
  };
  const note = (p, a) => {
    if (!a) return `<div><h3>${esc(p.name)}</h3><p class="dim">Not run yet.</p></div>`;
    const why = graded(a) ? "" : `<p>Not graded: ${esc(WHY[a.error_code] || a.error_code || "unknown error")}.</p>`;
    const summary = a.quality ? `<p>${esc(a.quality.summary)}</p>` : "";
    const flags = a.behavior?.flags?.length ? `<p>Flagged: ${a.behavior.flags.map((f) => esc(DATA.flags[f] || f)).join("; ")}.</p>` : "";
    return `<div><h3>${esc(p.name)}, ${day(a.date)}</h3>${why}${summary}${flags}</div>`;
  };
  $("tasks").innerHTML = `<div class="trow"><span>Task</span>${products.map((p) => `<span><span class="sw" style="background:${COLOR[p.key]}"></span>${esc(p.name)}, latest</span>`).join("")}</div>`
    + keys.map((k) => {
      const m = meta.get(k);
      return `<details id="${anchor(k)}"><summary><span class="tc"><span class="tn">${esc(k)}</span><span class="tk">${esc(m.difficulty)} · ${esc(KIND[m.category] || m.category)} · ${m.source === "mined" ? "real bug fix" : "benchmark task"}</span></span>`
        + products.map((p) => `<span class="res">${res(p, k)}</span>`).join("")
        + `</summary><div class="notes">${products.map((p) => note(p, latest[p.key].get(k))).join("")}</div></details>`;
    }).join("");
}

function openFromHash() {
  const target = location.hash.startsWith("#task-") && document.getElementById(location.hash.slice(1));
  if (!target) return;
  $("tasks-box").open = true;
  target.open = true;
  target.scrollIntoView({ block: "start" });
}

function drawRuns(DATA, products) {
  const byTrack = Object.fromEntries(products.map((p) => [p.track, p]));
  $("runs").innerHTML = `<caption>Every run</caption><thead><tr><th>Date</th><th>Product</th><th>Effort</th><th class="num">Passed</th><th class="num">Not graded</th><th class="num">Quality</th></tr></thead><tbody>`
    + [...DATA.runs].reverse().map((r) => {
      const p = byTrack[r.track];
      return `<tr><td>${day(r.date)}</td><td>${p ? `<span class="sw" style="background:${COLOR[p.key]}"></span>${esc(p.name)}` : esc(r.model)}</td><td>${esc(r.effort)}</td>`
        + `<td class="num">${r.passed} of ${r.graded}</td><td class="num">${r.not_graded}</td><td class="num">${r.quality == null ? "–" : r.quality.toFixed(2)}</td></tr>`;
    }).join("") + "</tbody>";
}
