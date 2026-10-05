"""Longitudinal dashboard for Grok, Astra, and optional Codex."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from sqlalchemy import text

from runner import CANONICAL_EFFORTS, CANONICAL_MODELS, CANONICAL_TRACKS, REQUEST_MODEL, SINGLE_AGENT_EFFORTS
from runner.storage import Store

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>Grok and Astra Longitudinal Monitor</title>
  <style>
    body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 32px; background: #0f1419; color: #e7ecf1; }}
    h1, h2, h3 {{ color: #fff; }}
    .sub {{ color: #9aa7b4; margin-top: -12px; }}
    .cards {{ display: flex; gap: 16px; flex-wrap: wrap; }}
    .card {{ background: #1b232c; padding: 16px 20px; border-radius: 12px; min-width: 220px; }}
    .card.aux {{ outline: 2px dashed #7aa2f7; }}
    .card.max {{ outline: 2px solid #f5c16c; }}
    .card strong {{ display: block; font-size: 26px; margin-top: 8px; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 16px; }}
    th, td {{ border-bottom: 1px solid #2b3642; padding: 8px 10px; text-left; }}
    img {{ max-width: 100%; background: #fff; border-radius: 8px; }}
    code {{ color: #9ecbff; }}
    .warn {{ color: #f5c16c; }}
  </style>
</head>
<body>
  <h1>Grok and Astra Longitudinal Monitor</h1>
  <p class="sub">Canonical experiment: two models × four efforts × two tracks &nbsp;|&nbsp; Frozen baseline: {baseline} &nbsp;|&nbsp; Suite: {suite}</p>
  <p>This dashboard reports observable end-to-end performance on a frozen private coding workload. It does not prove that a provider changed model weights. Longitudinal comparisons stay inside one model, one effort, and one track. There is no blended Grok score and no blended Astra score.</p>
  {series_cards}
  <h2>Four-level comparison series</h2>
  <p>low / medium / high / xhigh are independent. Astra <code>max</code> is auxiliary and is never blended into these charts. Codex / Sol is an optional unblended product series.</p>
  <img src="charts/pass-rate-by-series.png" alt="Pass rate by series"/>
  <h2>Operational reliability</h2>
  <p>Availability is valid model executions over scheduled attempts. It is not a quality score.</p>
  <img src="charts/operational-reliability.png" alt="Operational reliability"/>
  <h2>Largest regressions</h2>
  {regressions}
  <h2>Task-family slices</h2>
  {families}
  <h2>Attempt index</h2>
  {attempts}
  {codex_section}
  <h2>Provenance</h2>
  <pre>{config}</pre>
</body>
</html>
"""

ATTEMPT_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><title>Attempt {attempt_id}</title>
<style>body{{font-family:ui-sans-serif,system-ui,sans-serif;margin:32px;background:#0f1419;color:#e7ecf1}} pre{{white-space:pre-wrap}}</style>
</head><body>
<h1>Attempt {attempt_id}</h1>
<p><a href="../index.html">Back</a></p>
<h2>Identity</h2>
<pre>{identity}</pre>
<h2>Prompt</h2>
<pre>{prompt}</pre>
<h2>Environment manifest</h2>
<pre>{manifest}</pre>
<h2>Grader</h2>
<pre>{grader}</pre>
<h2>Final patch</h2>
<pre>{patch}</pre>
<h2>Raw response</h2>
<pre>{jsonl}</pre>
</body></html>
"""


def load_attempts(store: Store) -> pd.DataFrame:
    sql = """
    SELECT
        a.attempt_id, a.status, a.quality_status, a.completed_at, a.cost_usd_ticks, a.wall_time_ms,
        a.reasoning_tokens, a.input_tokens, a.track, a.client_mode, a.auth_surface,
        a.requested_effort, a.verified_effort, a.requested_model, a.verified_model,
        a.pair_key, a.scientific_data, a.source,
        t.task_key, t.task_version, t.category, t.difficulty,
        mc.reasoning_effort, mc.request_model, mc.config_sha256, mc.provider,
        s.strict_pass, s.partial_score, er.run_id, er.runner_git_sha, er.suite_hash,
        er.track AS run_track, er.scientific_data AS run_scientific
    FROM attempts a
    JOIN tasks t ON t.task_version_id = a.task_version_id
    JOIN eval_runs er ON er.run_id = a.run_id
    JOIN model_configs mc ON mc.config_id = er.config_id
    LEFT JOIN scores s ON s.attempt_id = a.attempt_id
    """
    with store.engine.begin() as conn:
        rows = conn.execute(text(sql)).mappings().all()
    return pd.DataFrame([dict(row) for row in rows])


def _effort_of(row: pd.Series) -> str:
    return str(row.get("requested_effort") or row.get("reasoning_effort") or "")


def _model_of(row: pd.Series) -> str:
    return str(row.get("requested_model") or row.get("request_model") or "")


def _quality_rate(slice_df: pd.DataFrame) -> tuple[str, str, str, int, int]:
    if slice_df.empty:
        return "n/a", "n/a", "n/a", 0, 0
    valid = slice_df[
        slice_df["quality_status"].isin(["quality_pass", "quality_fail"])
        | (slice_df["status"] == "completed")
    ]
    n_valid = len(valid)
    n_pass = (
        int((valid["quality_status"].eq("quality_pass") | valid["strict_pass"].fillna(False).astype(bool)).sum())
        if n_valid
        else 0
    )
    quality = f"{(n_pass / n_valid):.1%}" if n_valid else "n/a"
    avail = f"{(n_valid / len(slice_df)):.1%}" if len(slice_df) else "n/a"
    e2e = f"{(n_pass / len(slice_df)):.1%}" if len(slice_df) else "n/a"
    return quality, avail, e2e, n_pass, n_valid


def _regressions_html(analyses: dict[str, dict]) -> str:
    rows = ["<table><tr><th>Task</th><th>Family</th><th>Baseline</th><th>Current</th><th>Delta</th><th>Attempts</th></tr>"]
    collected = []
    for analysis in analyses.values():
        payload = analysis.get("payload") or {}
        for item in payload.get("largest_regressions") or []:
            collected.append(item)
    collected.sort(key=lambda item: item.get("delta", 0))
    if not collected:
        return "<p>Use compare against a locked baseline to populate largest regressions.</p>"
    for item in collected[:15]:
        rows.append(
            "<tr>"
            f"<td>{item.get('task')}</td>"
            f"<td>{item.get('family')}</td>"
            f"<td>{item.get('baseline_task_score')}</td>"
            f"<td>{item.get('current_task_score')}</td>"
            f"<td>{item.get('delta')}</td>"
            f"<td>{item.get('attempts')}</td>"
            "</tr>"
        )
    rows.append("</table>")
    return "".join(rows)


def _card(title: str, slice_df: pd.DataFrame, *, klass: str = "card") -> str:
    quality, avail, e2e, _n_pass, n_valid = _quality_rate(slice_df)
    tasks = slice_df["task_key"].nunique() if not slice_df.empty else 0
    repeats = (
        int(round(len(slice_df) / slice_df["task_key"].nunique()))
        if not slice_df.empty and slice_df["task_key"].nunique()
        else 0
    )
    return (
        f'<div class="{klass}"><h3>{title}</h3>'
        f"Quality given valid<strong>{quality}</strong>"
        f"<div>Availability {avail}</div><div>End-to-end {e2e}</div>"
        f"<div>Valid attempts {n_valid}</div>"
        f"<div>Tasks {tasks}</div>"
        f"<div>Repeats/task {repeats}</div></div>"
    )


def write_dashboard(store: Store, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    charts = out_dir / "charts"
    charts.mkdir(exist_ok=True)
    attempts_dir = out_dir / "attempts"
    attempts_dir.mkdir(exist_ok=True)
    df = load_attempts(store)
    baseline_name = "none"
    suite_name = "unknown"
    with store.engine.begin() as conn:
        row = conn.execute(text("SELECT name FROM baselines ORDER BY created_at DESC LIMIT 1")).fetchone()
        if row:
            baseline_name = str(row[0])
        srow = conn.execute(text("SELECT name FROM task_suites ORDER BY created_at DESC LIMIT 1")).fetchone()
        if srow:
            suite_name = str(srow[0])

    if df.empty:
        html = HTML.format(
            baseline=baseline_name,
            suite=suite_name,
            series_cards="<p>No attempts yet.</p>",
            regressions="<p>No regressions yet.</p>",
            families="<p>No family data.</p>",
            attempts="<p>No attempts.</p>",
            codex_section="",
            config="{}",
        )
        path = out_dir / "index.html"
        path.write_text(html, encoding="utf-8")
        (out_dir / "summary.json").write_text(json.dumps({"attempts": 0}, indent=2), encoding="utf-8")
        return path

    df["effort"] = df.apply(_effort_of, axis=1)
    df["model"] = df.apply(_model_of, axis=1)
    df["track"] = df["track"].fillna(df.get("run_track"))

    api = df[df["track"].isin(CANONICAL_TRACKS)].copy()
    four = api[api["effort"].isin(CANONICAL_EFFORTS)].copy()
    astra_max = api[(api["model"] == "gpt-6-astra") & (api["effort"] == "max")].copy()
    product = df[df["track"].fillna("") == "codex_product"].copy()
    product = product[~product["effort"].isin(["ultra"])]
    product = product[product["model"].fillna("").isin(["", REQUEST_MODEL])]
    claude_product = df[df["track"].fillna("") == "claude_code_product"].copy()

    analyses: dict[str, dict] = {}
    for item in store.list_analyses():
        key = f"{item.get('model')}/{item.get('effort')}/{item.get('track')}"
        if key not in analyses:
            analyses[key] = item

    cards = ['<div class="cards">']
    for model in CANONICAL_MODELS:
        for track in CANONICAL_TRACKS:
            for effort in CANONICAL_EFFORTS:
                slice_df = four[(four["model"] == model) & (four["track"] == track) & (four["effort"] == effort)]
                cards.append(_card(f"{model} · {effort} · {track}", slice_df))
    if not astra_max.empty:
        cards.append(_card("gpt-6-astra · max · auxiliary", astra_max, klass="card aux"))
    cards.append("</div>")

    labels = []
    rates = []
    colors = []
    palette = {"grok-4.6": "#7aa2f7", "gpt-6-astra": "#9ece6a"}
    for model in CANONICAL_MODELS:
        for track in CANONICAL_TRACKS:
            for effort in CANONICAL_EFFORTS:
                slice_df = four[(four["model"] == model) & (four["track"] == track) & (four["effort"] == effort)]
                valid = slice_df[
                    slice_df["quality_status"].isin(["quality_pass", "quality_fail"])
                    | (slice_df["status"] == "completed")
                ]
                if valid.empty:
                    continue
                passed = valid["quality_status"].eq("quality_pass") | valid["strict_pass"].fillna(False).astype(bool)
                labels.append(f"{model.split('-')[0]}/{effort}/{track[:3]}")
                rates.append(float(passed.mean() * 100))
                colors.append(palette.get(model, "#8aa0b4"))
    fig, ax = plt.subplots(figsize=(12, 4))
    if labels:
        ax.bar(labels, rates, color=colors)
    ax.set_ylim(0, 100)
    ax.set_ylabel("Quality given valid execution (%)")
    ax.set_title("Independent series — never blended across model, effort, or track")
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
    fig.tight_layout()
    fig.savefig(charts / "pass-rate-by-series.png", dpi=140)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(12, 4))
    avail_labels = []
    avail_rates = []
    for model in CANONICAL_MODELS:
        for track in CANONICAL_TRACKS:
            slice_df = four[(four["model"] == model) & (four["track"] == track)]
            if slice_df.empty:
                continue
            valid = slice_df["quality_status"].isin(["quality_pass", "quality_fail"]) | slice_df["status"].eq(
                "completed"
            )
            avail_labels.append(f"{model}/{track}")
            avail_rates.append(float(valid.mean() * 100))
    if avail_labels:
        ax.bar(avail_labels, avail_rates, color="#6cb6ff")
    ax.set_ylim(0, 100)
    ax.set_ylabel("Operational availability (%)")
    ax.set_title("Valid model executions / scheduled attempts")
    plt.setp(ax.get_xticklabels(), rotation=20, ha="right")
    fig.tight_layout()
    fig.savefig(charts / "operational-reliability.png", dpi=140)
    plt.close(fig)

    # Keep the legacy Codex filenames so older links do not 404.
    if not product.empty:
        fig, ax = plt.subplots(figsize=(9, 4))
        rates = []
        for effort in SINGLE_AGENT_EFFORTS:
            slice_df = product[product["effort"] == effort]
            valid = slice_df[
                slice_df["quality_status"].isin(["quality_pass", "quality_fail"])
                | (slice_df["status"] == "completed")
            ]
            if valid.empty:
                rates.append(0)
            else:
                passed = valid["quality_status"].eq("quality_pass") | valid["strict_pass"].fillna(False).astype(bool)
                rates.append(float(passed.mean() * 100))
        ax.bar(list(SINGLE_AGENT_EFFORTS), rates, color=["#8aa0b4", "#8aa0b4", "#8aa0b4", "#8aa0b4", "#f5c16c"])
        ax.set_ylim(0, 100)
        ax.set_title("Optional Codex / GPT-5.6 Sol series (unblended)")
        fig.tight_layout()
        fig.savefig(charts / "pass-rate-by-effort.png", dpi=140)
        plt.close(fig)

    family_source = four if not four.empty else product
    family_rows = ["<table><tr><th>Family</th><th>Quality</th><th>Attempts</th></tr>"]
    if not family_source.empty:
        grouped = family_source.groupby("category")
        for family, group in grouped:
            valid = group[group["quality_status"].isin(["quality_pass", "quality_fail"]) | (group["status"] == "completed")]
            rate = valid["strict_pass"].fillna(0).astype(float).mean() if not valid.empty else 0
            family_rows.append(f"<tr><td>{family}</td><td>{rate:.1%}</td><td>{len(group)}</td></tr>")
    family_rows.append("</table>")

    shown = four if not four.empty else product
    attempt_rows = ["<table><tr><th>Attempt</th><th>Model</th><th>Task</th><th>Effort</th><th>Track</th><th>Status</th><th>Pass</th></tr>"]
    for _, row in shown.head(200).iterrows():
        attempt_rows.append(
            f'<tr><td><a href="attempts/{row["attempt_id"]}.html">{str(row["attempt_id"])[:8]}</a></td>'
            f'<td>{row.get("model")}</td><td>{row["task_key"]}</td><td>{row["effort"]}</td>'
            f'<td>{row.get("track")}</td>'
            f'<td>{row.get("quality_status") or row["status"]}</td>'
            f'<td>{"\u2713" if row.get("strict_pass") else "\u2717"}</td></tr>'
        )
        _write_attempt_page(store, attempts_dir / f"{row['attempt_id']}.html", str(row["attempt_id"]))
    attempt_rows.append("</table>")

    codex_section = ""
    if not product.empty:
        codex_cards = ['<h2>Optional Codex / GPT-5.6 Sol series</h2><p>Ultra is excluded. This series is never mixed into Grok or Astra cards.</p><div class="cards">']
        for effort in SINGLE_AGENT_EFFORTS:
            klass = "card max" if effort == "max" else "card"
            codex_cards.append(_card(f"gpt-5.6-sol · {effort} · codex_product", product[product["effort"] == effort], klass=klass))
        codex_cards.append("</div>")
        if (charts / "pass-rate-by-effort.png").exists():
            codex_cards.append('<img src="charts/pass-rate-by-effort.png" alt="Codex pass rate by effort"/>')
        codex_section = "".join(codex_cards)

    if not claude_product.empty:
        claude_cards = [
            "<h2>Optional Claude Code product series</h2><p>Claude Code on a Claude subscription. "
            "Never mixed into API or Codex cards; max is auxiliary.</p><div class=\"cards\">"
        ]
        for model in sorted(set(claude_product["model"].dropna().astype(str))):
            for effort in SINGLE_AGENT_EFFORTS:
                slice_df = claude_product[(claude_product["model"] == model) & (claude_product["effort"] == effort)]
                if slice_df.empty:
                    continue
                klass = "card max" if effort == "max" else "card"
                claude_cards.append(_card(f"{model} · {effort} · claude_code_product", slice_df, klass=klass))
        claude_cards.append("</div>")
        codex_section += "".join(claude_cards)

    config_bits = {
        "models": sorted(set(df["model"].dropna().astype(str))),
        "efforts": sorted(set(df["effort"].dropna().astype(str))),
        "tracks": sorted(set(df["track"].dropna().astype(str))),
        "runner_shas": sorted(set(df["runner_git_sha"].dropna()))[:8],
        "note": (
            "Never blend Grok with Astra, model_only with agentic, or Astra max with the four-level set. "
            "Codex / Sol is optional and unblended. Ultra is excluded from single-agent charts."
        ),
    }
    html = HTML.format(
        baseline=baseline_name,
        suite=suite_name,
        series_cards="".join(cards),
        regressions=_regressions_html(analyses),
        families="".join(family_rows),
        attempts="".join(attempt_rows),
        codex_section=codex_section,
        config=json.dumps(config_bits, indent=2),
    )
    path = out_dir / "index.html"
    path.write_text(html, encoding="utf-8")
    (out_dir / "summary.json").write_text(
        json.dumps({"attempts": len(df), "baseline": baseline_name, "suite": suite_name}, indent=2),
        encoding="utf-8",
    )
    return path


def _write_attempt_page(store: Store, path: Path, attempt_id: str) -> None:
    row = store.fetch_attempt(attempt_id) or {"attempt_id": attempt_id}
    artifacts = {item["kind"]: item for item in store.list_artifacts(attempt_id)}
    manifest = store.get_environment_manifest(str(row.get("run_id") or "")) or {}
    prompt = ""
    jsonl = ""
    patch = ""
    grader = row.get("grading_details") or ""
    item = artifacts.get("manifest.json")
    if item and Path(item["path"]).exists():
        try:
            payload = json.loads(Path(item["path"]).read_text(encoding="utf-8"))
            prompt = payload.get("prompt") or prompt
        except json.JSONDecodeError:
            pass
    for kind in ("raw_codex.jsonl", "raw_response.json"):
        if artifacts.get(kind) and Path(artifacts[kind]["path"]).exists():
            jsonl = Path(artifacts[kind]["path"]).read_text(encoding="utf-8")[-8000:]
            break
    if artifacts.get("final.patch") and Path(artifacts["final.patch"]["path"]).exists():
        patch = Path(artifacts["final.patch"]["path"]).read_text(encoding="utf-8")[-8000:]
    path.write_text(
        ATTEMPT_HTML.format(
            attempt_id=attempt_id,
            identity=json.dumps(
                {
                    "task": row.get("task_key"),
                    "version": row.get("task_version"),
                    "requested_model": row.get("requested_model"),
                    "verified_model": row.get("verified_model"),
                    "requested_effort": row.get("requested_effort"),
                    "verified_effort": row.get("verified_effort"),
                    "track": row.get("track"),
                    "pair_key": row.get("pair_key"),
                    "quality_status": row.get("quality_status"),
                    "scientific_data": row.get("scientific_data"),
                },
                indent=2,
            ),
            prompt=prompt or "(prompt stored in attempt manifest)",
            manifest=json.dumps(manifest, indent=2, default=str)[:8000],
            grader=str(grader)[:4000],
            patch=patch or "(no patch)",
            jsonl=jsonl or "(no raw response)",
        ),
        encoding="utf-8",
    )
