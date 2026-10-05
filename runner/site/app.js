"use strict";
// Renders data.json (written by `python -m runner site`) into the report.

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const mean = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
const median = (xs) => {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b), m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};
const pct = (x) => (x == null ? "–" : `${Math.round(x * 100)}%`);
const graded = (a) => a.quality_status === "quality_pass" || a.quality_status === "quality_fail";
const passed = (a) => a.quality_status === "quality_pass";
const day = (d, opts = { month: "short", day: "numeric" }) => new Date(`${d}T00:00:00Z`).toLocaleDateString("en-GB", { ...opts, timeZone: "UTC" });
const addDays = (d, n) => { const t = new Date(`${d}T00:00:00Z`); t.setUTCDate(t.getUTCDate() + n); return t.toISOString().slice(0, 10); };
const COLORS = { opus: "var(--opus)", grok: "var(--grok)" };
const WHY = {
  usage_limit: "the plan's usage limit was reached", auth: "sign-in failed", model_mismatch: "a different model was served",
  effort_mismatch: "a different reasoning level was served", web_access: "the agent reached the web",
  isolation_unavailable: "the product was not set up", container_restart: "the machine restarted",
  no_result_event: "the product stopped without a result", timeout: "it ran out of time",
};

fetch("data.json", { cache: "no-store" })
  .then((r) => { if (!r.ok) throw new Error(`data.json: HTTP ${r.status}`); return r.json(); })
  .then(render)
  .catch((err) => { $("headline").textContent = "The latest results could not be loaded."; $("standfirst").innerHTML = `<p>${esc(err.message)}</p>`; });

function render(DATA) {
  // One series per product: the effort of its newest run. Runs at other efforts stay in the run log.
  const products = DATA.products.map((p) => {
    const runs = DATA.runs.filter((r) => r.track === p.track);
    const effort = runs.length ? runs[runs.length - 1].effort : null;
    return { ...p, key: p.track.startsWith("cursor") ? "grok" : "opus", effort,
      attempts: DATA.attempts.filter((a) => a.track === p.track && a.effort === effort) };
  });
  const live = products.filter((p) => p.attempts.length);
  const dates = [...new Set(live.flatMap((p) => p.attempts.map((a) => a.date)))].sort();
  const last = dates[dates.length - 1];
  const weekStart = last ? addDays(last, -6) : null;
  const inWeek = (a) => last && a.date >= weekStart && a.date <= last;
  const stats = (list) => {
    const g = list.filter(graded), p = g.filter(passed);
    return { graded: g.length, passed: p.length, excluded: list.length - g.length, rate: g.length ? p.length / g.length : null,
      quality: mean(list.filter((a) => a.quality).map((a) => a.quality.overall)),
      minutes: median(g.map((a) => a.minutes).filter(Boolean)) };
  };

  const updated = new Date(DATA.generated_at);
  $("dateline").textContent = `Updated ${updated.toLocaleString("en-GB", { day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" })} UTC`
    + (DATA.next_run ? ` · Next run ${new Date(DATA.next_run).toLocaleString("en-GB", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit", timeZone: "UTC" })} UTC` : "");

  // ---- Verdict, in words
  const verdict = (p) => {
    if (!p.attempts.length) return { state: "off", text: `${p.name} on ${p.via} has not run yet. ${p.setup}` };
    const m = (DATA.monitor || []).find((x) => x.series === `${p.model}/${p.effort}/${p.track}`);
    if (m && m.status === "alarm") return { state: "alarm", text: m.message };
    if (m && m.status === "no_alarm") return { state: "ok" };
    const first = p.attempts.map((a) => a.date).sort()[0];
    const n = Math.min(7, Math.round((Date.parse(last) - Date.parse(first)) / 864e5) + 1);
    return { state: "baseline", day: n, until: addDays(first, 6) };
  };
  const vs = live.map((p) => ({ p, v: verdict(p), s: stats(p.attempts.filter(inWeek)) }));
  const alarms = vs.filter((x) => x.v.state === "alarm");
  $("headline").textContent = !live.length ? "No results yet."
    : alarms.length ? `${alarms.map((x) => `${x.p.name} on ${x.p.via}`).join(" and ")} ${alarms.length > 1 ? "are" : "is"} getting worse.`
    : vs.every((x) => x.v.state === "ok") ? "Neither product shows a sustained decline."
    : vs.every((x) => x.v.state === "baseline") ? "Too early for a verdict: both products are still in their first week."
    : "No sustained decline so far.";
  const sentence = ({ p, v, s }) => {
    const sw = `<span class="swatch" style="background:${COLORS[p.key]}"></span>`;
    let t = `${sw}<b>${esc(p.name)} on ${esc(p.via)}</b> passed ${s.passed} of ${s.graded} graded tasks in the last seven days`;
    if (s.quality != null) t += `, with a mean quality score of ${s.quality.toFixed(1)} out of 5`;
    t += ".";
    if (s.excluded) t += ` ${s.excluded === 1 ? "One more attempt was" : `${s.excluded} more attempts were`} excluded (see the task list).`;
    if (v.state === "ok") t += " The drift monitor finds no sustained drop against its first week.";
    if (v.state === "alarm") t += ` ${esc(v.text)}`;
    if (v.state === "baseline") t += ` This is day ${v.day} of its seven-day baseline; the drift check starts after ${day(v.until, { day: "numeric", month: "long" })}.`;
    return `<p>${t}</p>`;
  };
  $("standfirst").innerHTML = vs.map(sentence).join("")
    + products.filter((p) => !p.attempts.length).map((p) => `<p>${esc(p.name)} on ${esc(p.via)} has not run yet. ${esc(p.setup)}</p>`).join("");

  drawPassChart(live, dates);

  // ---- Same tasks, same day
  (() => {
    const [a, b] = products;
    const idx = (p) => new Map(p.attempts.filter(graded).map((x) => [`${x.date}|${x.task_key}`, x]));
    const A = idx(a), B = idx(b);
    const keys = [...A.keys()].filter((k) => B.has(k));
    if (!keys.length) { $("h2h").textContent = "There are no task-days both products completed yet."; return; }
    const c = { both: 0, a: 0, b: 0, none: 0 };
    keys.forEach((k) => { const pa = passed(A.get(k)), pb = passed(B.get(k)); c[pa && pb ? "both" : pa ? "a" : pb ? "b" : "none"]++; });
    const q = (M) => mean(keys.map((k) => M.get(k).quality?.overall).filter((x) => x != null));
    const qa = q(A), qb = q(B);
    $("h2h").innerHTML = `On the ${keys.length} task-days both products completed, both passed ${c.both}, only ${esc(a.name)} passed ${c.a}, `
      + `only ${esc(b.name)} passed ${c.b}, and neither passed ${c.none}.`
      + (qa != null && qb != null ? ` Their mean quality scores on those tasks were ${qa.toFixed(2)} and ${qb.toFixed(2)}.` : "")
      + (keys.length < 30 ? " That is too few to call one product better; the gap means more once it holds for a few weeks." : "");
  })();

  // ---- Comparison tables
  const head = (label) => `<thead><tr><th>${label}</th>${live.map((p) => `<th class="num"><span class="swatch" style="background:${COLORS[p.key]}"></span>${esc(p.name)}</th>`).join("")}</tr></thead>`;
  const recent = (p) => p.attempts.filter((a) => graded(a) && inWeek(a));
  (() => {
    const withB = Object.fromEntries(live.map((p) => [p.key, recent(p).filter((a) => a.behavior)]));
    if (!live.some((p) => withB[p.key].length)) { $("behaviour").outerHTML = `<p class="muted">No behaviour data yet.</p>`; return; }
    const share = (p, f) => { const xs = withB[p.key]; return xs.length ? xs.filter(f).length / xs.length : null; };
    const rows = Object.entries(DATA.flags).map(([flag, text]) => [text.charAt(0).toUpperCase() + text.slice(1), (p) => pct(share(p, (a) => a.behavior.flags.includes(flag)))]);
    rows.push(["Median tool calls per attempt", (p) => median(withB[p.key].map((a) => a.behavior.tool_calls ?? 0)) ?? "–"]);
    rows.push(["Median test runs per attempt", (p) => median(withB[p.key].map((a) => a.behavior.test_runs ?? 0)) ?? "–"]);
    rows.push(["Attempts measured", (p) => withB[p.key].length]);
    $("behaviour").innerHTML = head("Habit") + `<tbody>${rows.map(([label, f]) => `<tr><td>${esc(label)}</td>${live.map((p) => `<td class="num">${f(p)}</td>`).join("")}</tr>`).join("")}</tbody>`;
  })();
  (() => {
    const scored = Object.fromEntries(live.map((p) => [p.key, recent(p).filter((a) => a.quality)]));
    if (!live.some((p) => scored[p.key].length)) { $("quality").outerHTML = `<p class="muted">No quality scores yet.</p>`; return; }
    const rows = Object.entries(DATA.dimensions).map(([dim, question]) => {
      const cells = live.map((p) => { const v = mean(scored[p.key].map((a) => a.quality.dims[dim]).filter((x) => x != null)); return `<td class="num">${v == null ? "–" : v.toFixed(1)}</td>`; }).join("");
      return `<tr><td>${esc(dim.replace(/_/g, " ").replace(/^./, (ch) => ch.toUpperCase()))}<span class="sub">${esc(question)}</span></td>${cells}</tr>`;
    });
    rows.push(`<tr><td>Attempts scored</td>${live.map((p) => `<td class="num">${scored[p.key].length}</td>`).join("")}</tr>`);
    $("quality").innerHTML = head("Dimension") + `<tbody>${rows.join("")}</tbody>`;
  })();

  // ---- Every task
  (() => {
    const latest = Object.fromEntries(products.map((p) => {
      const m = new Map();
      p.attempts.forEach((a) => { const o = m.get(a.task_key); if (!o || a.date >= o.date) m.set(a.task_key, a); });
      return [p.key, m];
    }));
    const meta = new Map(live.flatMap((p) => p.attempts).map((a) => [a.task_key, a]));
    const order = { hard: 0, medium: 1, easy: 2 };
    const keys = [...meta.keys()].sort((x, y) => (order[meta.get(x).difficulty] ?? 1) - (order[meta.get(y).difficulty] ?? 1) || x.localeCompare(y));
    const result = (a) => {
      if (!a) return `<span class="muted">Not run</span>`;
      const bits = [day(a.date), a.minutes ? `${a.minutes.toFixed(1)} min` : null, a.quality ? `quality ${a.quality.overall.toFixed(1)}` : null].filter(Boolean).join(" · ");
      const label = a.quality_status === "quality_pass" ? "Passed"
        : a.quality_status === "quality_fail" ? `<span class="fail">✗ ${a.error_code === "timeout" ? "Timed out" : "Failed"}</span>`
        : `<span class="muted">Excluded</span>`;
      return `${label}<small>${bits}</small>`;
    };
    const note = (p, a) => {
      if (!a) return `<div><h3>${esc(p.name)}</h3><p class="muted">Not run yet.</p></div>`;
      const why = graded(a) ? "" : `<p>Excluded because ${esc(WHY[a.error_code] || a.error_code || "of an unknown error")}.</p>`;
      const summary = a.quality ? `<p>${esc(a.quality.summary)}</p>` : "";
      const claims = a.quality?.claims?.length ? `<p class="flags">Claims the diff doesn't support: ${a.quality.claims.map(esc).join(" ")}</p>` : "";
      const flags = a.behavior ? `<p class="flags">${a.behavior.flags.length ? `Flagged: ${a.behavior.flags.map((f) => esc(DATA.flags[f] || f)).join("; ")}.` : "No behaviour flags."}</p>` : "";
      return `<div><h3>${esc(p.name)}, ${day(a.date)}</h3>${why}${summary}${claims}${flags}</div>`;
    };
    $("tasks").innerHTML = `<div class="taskhead"><span>Task</span>${products.map((p) => `<span><span class="swatch" style="background:${COLORS[p.key]}"></span>${esc(p.name)}</span>`).join("")}</div>`
      + keys.map((k) => {
        const m = meta.get(k);
        return `<details><summary><span class="tcell"><span class="tname">${esc(k)}</span><span class="tkind">${esc(m.difficulty)} · ${m.source === "mined" ? "real bug fix" : "benchmark task"}</span></span>`
          + products.map((p) => `<span class="res">${result(latest[p.key].get(k))}</span>`).join("")
          + `</summary><div class="notes">${products.map((p) => note(p, latest[p.key].get(k))).join("")}</div></details>`;
      }).join("");
  })();

  // ---- Runs
  const byTrack = Object.fromEntries(products.map((p) => [p.track, p]));
  $("runs").innerHTML = `<thead><tr><th>Date</th><th>Product</th><th>Effort</th><th class="num">Passed</th><th class="num">Excluded</th><th class="num">Quality</th><th class="num">Median time</th></tr></thead><tbody>`
    + [...DATA.runs].reverse().map((r) => {
      const p = byTrack[r.track];
      return `<tr><td>${day(r.date)}</td><td>${p ? `<span class="swatch" style="background:${COLORS[p.key]}"></span>${esc(p.name)}` : esc(r.model)}</td><td>${esc(r.effort)}</td>`
        + `<td class="num">${r.passed} of ${r.graded}</td><td class="num">${r.not_graded}</td><td class="num">${r.quality == null ? "–" : r.quality.toFixed(2)}</td>`
        + `<td class="num">${r.median_minutes == null ? "–" : `${r.median_minutes} min`}</td></tr>`;
    }).join("") + "</tbody>";
}

function drawPassChart(live, dates) {
  const host = $("chart-pass");
  const series = live.map((p) => ({ p, pts: dates.map((d) => {
    const g = p.attempts.filter((a) => a.date === d && graded(a));
    return { d, n: g.length, k: g.filter(passed).length, v: g.length ? g.filter(passed).length / g.length : null };
  }) }));
  if (!dates.length) { host.innerHTML = `<p class="muted">Nothing to chart yet.</p>`; return; }
  const W = 760, H = 300, L = 44, R = 132, T = 14, B = 34;
  const x = (i) => (dates.length === 1 ? L + (W - L - R) / 2 : L + (i * (W - L - R)) / (dates.length - 1));
  const lo = 0.5;
  const y = (v) => T + (1 - (Math.max(v, lo) - lo) / (1 - lo)) * (H - T - B);
  const every = Math.max(1, Math.ceil(dates.length / 8));
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Strict pass rate by day for ${live.map((p) => p.name).join(" and ")}">`;
  [0.5, 0.75, 1].forEach((t) => {
    svg += `<line class="${t === lo ? "baseline" : "gridline"}" x1="${L}" x2="${W - R}" y1="${y(t)}" y2="${y(t)}"/>`;
    svg += `<text x="${L - 8}" y="${y(t) + 4}" text-anchor="end">${Math.round(t * 100)}%</text>`;
  });
  dates.forEach((d, i) => { if (i % every === 0 || i === dates.length - 1) svg += `<text x="${x(i)}" y="${H - 10}" text-anchor="middle">${day(d)}</text>`; });
  const ends = [];
  series.forEach(({ p, pts }) => {
    let path = "", pen = false;
    pts.forEach((pt, i) => { if (pt.v == null) { pen = false; return; } path += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(pt.v).toFixed(1)}`; pen = true; });
    if (path) svg += `<path d="${path}" fill="none" stroke="${COLORS[p.key]}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
    pts.forEach((pt, i) => { if (pt.v != null) svg += `<circle cx="${x(i)}" cy="${y(pt.v)}" r="4.5" fill="${COLORS[p.key]}" stroke="var(--paper)" stroke-width="2"/>`; });
    const lastPt = [...pts].reverse().find((pt) => pt.v != null);
    if (lastPt) ends.push({ p, y: y(lastPt.v), v: lastPt.v });
  });
  // Direct labels at the right edge, nudged apart so they never overlap.
  ends.sort((a, b) => a.y - b.y);
  for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 18) ends[i].y = ends[i - 1].y + 18;
  ends.forEach((e) => { svg += `<text class="label" x="${W - R + 14}" y="${e.y + 5}" style="fill:${COLORS[e.p.key]}">${esc(e.p.name)} ${pct(e.v)}</text>`; });
  svg += `<line class="cursor" id="pass-cursor" x1="0" x2="0" y1="${T}" y2="${H - B}" visibility="hidden"/>`;
  svg += `<rect id="pass-hit" x="${L - 20}" y="${T}" width="${W - L - R + 40}" height="${H - T - B}" fill="transparent"/></svg>`;
  host.innerHTML = svg;
  $("chart-pass-note").textContent = "Each day runs a different quarter of the tasks, so single days move with the mix. The axis starts at 50%."
    + (dates.length < 4 ? " The trend becomes readable after a week or two." : "");

  // Hover: a crosshair on the nearest day and a tooltip with each product's result.
  const svgEl = host.querySelector("svg"), hit = $("pass-hit"), cursor = $("pass-cursor"), tip = $("tooltip");
  const show = (evt) => {
    const box = svgEl.getBoundingClientRect(), px = ((evt.clientX - box.left) / box.width) * W;
    let i = 0, best = Infinity;
    dates.forEach((_, j) => { const dist = Math.abs(x(j) - px); if (dist < best) { best = dist; i = j; } });
    cursor.setAttribute("x1", x(i)); cursor.setAttribute("x2", x(i)); cursor.setAttribute("visibility", "visible");
    tip.innerHTML = `<b>${day(dates[i], { day: "numeric", month: "long" })}</b><br>` + series.map(({ p, pts }) => {
      const pt = pts[i];
      return `<span class="swatch" style="background:${COLORS[p.key]}"></span>${esc(p.name)}: ${pt.v == null ? "no graded tasks" : `${pt.k} of ${pt.n} passed (${pct(pt.v)})`}`;
    }).join("<br>");
    tip.hidden = false;
    const left = Math.min(evt.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
    tip.style.left = `${Math.max(8, left)}px`; tip.style.top = `${evt.clientY + 14}px`;
  };
  const hide = () => { cursor.setAttribute("visibility", "hidden"); tip.hidden = true; };
  hit.addEventListener("pointermove", show);
  hit.addEventListener("pointerdown", show);
  hit.addEventListener("pointerleave", hide);
}
