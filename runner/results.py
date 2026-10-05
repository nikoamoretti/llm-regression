"""Keep the results database on a git branch so ephemeral runs share one history.

Cloud sessions start on a fresh machine, so a scheduled run pulls the database
from the ``results`` branch, appends its attempts, and pushes it back with the
drift report. The branch holds only data (``regression.db.gz``, ``runs.jsonl``,
``monitor/`` and the built results site in ``site/``) and is written with git plumbing, never by checking it out.

``push`` refuses when the branch moved since ``pull``: the local database
would overwrite attempts it never saw.

    python -m runner results pull
    python -m runner results push --summary '{"trigger": "schedule"}'
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BRANCH = "results"
DB_BLOB = "regression.db.gz"
BASE_FILE = "results-base.txt"


class ResultsError(RuntimeError):
    pass


def _git(root: Path, *args: str, input: bytes | None = None, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", "-C", str(root), *args], input=input, capture_output=True, check=False)
    if check and proc.returncode != 0:
        raise ResultsError(f"git {' '.join(args[:3])} failed: {proc.stderr.decode(errors='replace').strip()[:500]}")
    return proc


def _retry(action, attempts: int = 4):
    delay = 2
    for attempt in range(attempts):
        try:
            return action()
        except ResultsError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)
            delay *= 2
    return None


def remote_head(root: Path, remote: str = "origin") -> str | None:
    """Current tip of the results branch on ``remote`` (fetched), or None if it does not exist yet."""
    proc = _git(root, "ls-remote", "--heads", remote, BRANCH, check=False)
    if proc.returncode != 0:
        raise ResultsError(f"cannot reach {remote}: {proc.stderr.decode(errors='replace').strip()[:300]}")
    if not proc.stdout.strip():
        return None
    _retry(lambda: _git(root, "fetch", "-q", remote, f"+refs/heads/{BRANCH}:refs/remotes/{remote}/{BRANCH}"))
    return _git(root, "rev-parse", f"refs/remotes/{remote}/{BRANCH}").stdout.decode().strip()


def pull(root: Path, db_path: Path, *, remote: str = "origin", state_dir: Path | None = None) -> str | None:
    """Restore ``db_path`` from the branch; returns the commit it came from (None: no history yet)."""
    state = state_dir or db_path.parent
    head = remote_head(root, remote)
    if head is None:
        if db_path.exists():
            raise ResultsError(f"{db_path} exists but the {BRANCH} branch does not; move it aside or push it first")
    else:
        data = _git(root, "show", f"{head}:{DB_BLOB}").stdout
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_path.write_bytes(gzip.decompress(data))
    state.mkdir(parents=True, exist_ok=True)
    (state / BASE_FILE).write_text((head or "") + "\n", encoding="utf-8")
    return head


def _blob(root: Path, data: bytes) -> str:
    return _git(root, "hash-object", "-w", "--stdin", input=data).stdout.decode().strip()


def _tree(root: Path, entries: dict[str, str | dict]) -> str:
    """Values: a blob id, ``tree:<id>`` for an existing tree, or a dict for a new subtree."""
    lines = []
    for name, value in sorted(entries.items()):
        if isinstance(value, dict):
            lines.append(f"040000 tree {_tree(root, value)}\t{name}")
        elif value.startswith("tree:"):
            lines.append(f"040000 tree {value.removeprefix('tree:')}\t{name}")
        else:
            lines.append(f"100644 blob {value}\t{name}")
    return _git(root, "mktree", input=("\n".join(lines) + "\n").encode()).stdout.decode().strip()


def _dir_entries(root: Path, directory: Path) -> dict[str, str | dict]:
    """A directory as nested tree entries (files become blobs, subdirectories subtrees)."""
    out: dict[str, str | dict] = {}
    for path in sorted(directory.iterdir()):
        if path.is_dir():
            out[path.name] = _dir_entries(root, path)
        elif path.is_file():
            out[path.name] = _blob(root, path.read_bytes())
    return out


def push(
    root: Path,
    db_path: Path,
    *,
    monitor_dir: Path | None = None,
    site_dir: Path | None = None,
    summary: dict[str, Any] | None = None,
    remote: str = "origin",
    state_dir: Path | None = None,
    message: str | None = None,
) -> str:
    """Commit the database (and drift report) on top of the pulled tip and push it."""
    state = state_dir or db_path.parent
    base_file = state / BASE_FILE
    if not base_file.exists():
        raise ResultsError("run `results pull` before `results push`")
    base = base_file.read_text(encoding="utf-8").strip() or None
    head = remote_head(root, remote)
    if head != base:
        raise ResultsError(f"the {BRANCH} branch moved since pull ({base} -> {head}); pull and re-run instead")
    if not db_path.exists():
        raise ResultsError(f"no database at {db_path}")
    # Fold the WAL sidecar into the main file so the pushed copy holds every attempt.
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    runs = _git(root, "show", f"{head}:runs.jsonl", check=False).stdout if head else b""
    record = {"pushed_at": datetime.now(timezone.utc).isoformat(), **(summary or {})}
    runs += (json.dumps(record, sort_keys=True) + "\n").encode()
    entries: dict[str, str | dict] = {
        DB_BLOB: _blob(root, gzip.compress(db_path.read_bytes(), mtime=0)),
        "runs.jsonl": _blob(root, runs),
    }
    for name, directory in (("monitor", monitor_dir), ("site", site_dir)):
        if directory and directory.is_dir():
            entries[name] = _dir_entries(root, directory)
        elif head:
            # Nothing new this run: keep the last one.
            previous = _git(root, "rev-parse", "--verify", "-q", f"{head}:{name}", check=False)
            if previous.returncode == 0:
                entries[name] = "tree:" + previous.stdout.decode().strip()
    tree = _tree(root, entries)
    args = ["commit-tree", tree, "-m", message or f"Results: {record['pushed_at']}"]
    if head:
        args[2:2] = ["-p", head]
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": os.environ.get("GIT_AUTHOR_NAME", "llm-regression"),
        "GIT_AUTHOR_EMAIL": os.environ.get("GIT_AUTHOR_EMAIL", "llm-regression@users.noreply.github.com"),
    }
    env.setdefault("GIT_COMMITTER_NAME", env["GIT_AUTHOR_NAME"])
    env.setdefault("GIT_COMMITTER_EMAIL", env["GIT_AUTHOR_EMAIL"])
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, env=env, check=False)
    if proc.returncode != 0:
        raise ResultsError(f"git commit-tree failed: {proc.stderr.decode(errors='replace').strip()[:300]}")
    commit = proc.stdout.decode().strip()
    # A plain (non-force) push: if someone else pushed meanwhile, it is rejected.
    _retry(lambda: _git(root, "push", "-q", remote, f"{commit}:refs/heads/{BRANCH}"))
    base_file.write_text(commit + "\n", encoding="utf-8")
    return commit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="runner results", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("pull", "push"):
        action = sub.add_parser(name)
        action.add_argument("--db", type=Path, default=Path("artifacts/regression.db"))
        action.add_argument("--remote", default="origin")
    sub.choices["push"].add_argument("--monitor-dir", type=Path, default=Path("artifacts/monitor"))
    sub.choices["push"].add_argument("--site-dir", type=Path, default=Path("artifacts/site"))
    sub.choices["push"].add_argument("--summary", default="{}", help="JSON object appended to runs.jsonl")
    args = parser.parse_args(argv)
    root = Path.cwd()
    try:
        if args.action == "pull":
            head = pull(root, args.db, remote=args.remote)
            print(f"restored {args.db} from {BRANCH}@{head[:12]}" if head else f"no {BRANCH} branch yet; starting fresh")
        else:
            commit = push(root, args.db, monitor_dir=args.monitor_dir, site_dir=args.site_dir,
                          summary=json.loads(args.summary),
                          remote=args.remote)
            print(f"pushed {BRANCH}@{commit[:12]}")
    except ResultsError as exc:
        print(f"results: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
