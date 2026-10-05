from __future__ import annotations

import gzip
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from runner.results import BRANCH, ResultsError, pull, push


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _db(path: Path, *rows: str) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS attempts (name TEXT)")
        conn.executemany("INSERT INTO attempts VALUES (?)", [(row,) for row in rows])


def _rows(data: bytes, tmp: Path) -> list[str]:
    tmp.write_bytes(data)
    with sqlite3.connect(tmp) as conn:
        return [row for (row,) in conn.execute("SELECT name FROM attempts ORDER BY rowid")]


def _clone(origin: Path, path: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(origin), str(path)], check=True, capture_output=True)
    return path


@pytest.fixture
def origin(tmp_path) -> Path:
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    (seed / "README.md").write_text("harness\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "init")
    _git(seed, "push", "-q", str(bare), "main")
    return bare


def test_runs_on_fresh_machines_accumulate_one_history(origin, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@t")
    first = _clone(origin, tmp_path / "day1")
    db = first / "artifacts" / "regression.db"
    assert pull(first, db) is None and not db.exists()
    _db(db, "day-1")  # pull created artifacts/ for its state file
    monitor = first / "artifacts" / "monitor"
    monitor.mkdir()
    (monitor / "monitor.json").write_text("[]")
    site = first / "artifacts" / "site"
    (site / "assets").mkdir(parents=True)
    (site / "index.html").write_text("<title>Nerf Watch</title>")
    (site / "assets" / "app.js").write_text("// app")
    push(first, db, monitor_dir=monitor, site_dir=site, summary={"statuses": {"quality_pass": 3}})

    # Day two: a fresh clone sees day one's database and appends to it.
    second = _clone(origin, tmp_path / "day2")
    db2 = second / "artifacts" / "regression.db"
    assert pull(second, db2)
    assert _rows(db2.read_bytes(), tmp_path / "check.db") == ["day-1"]
    _db(db2, "day-2")
    push(second, db2, summary={"statuses": {"quality_fail": 1}})

    third = _clone(origin, tmp_path / "day3")
    _git(third, "fetch", "-q", "origin", BRANCH)
    tip = f"origin/{BRANCH}"
    blob = subprocess.run(["git", "-C", str(third), "show", f"{tip}:regression.db.gz"],
                          capture_output=True, check=True).stdout
    # Rows still in the WAL sidecar at push time are included.
    assert _rows(gzip.decompress(blob), tmp_path / "final.db") == ["day-1", "day-2"]
    runs = [json.loads(line) for line in _git(third, "show", f"{tip}:runs.jsonl").splitlines()]
    assert [r["statuses"] for r in runs] == [{"quality_pass": 3}, {"quality_fail": 1}]
    assert _git(third, "show", f"{tip}:monitor/monitor.json") == "[]"
    # The site, nested directories included, is published; day two built none, so day one's is kept.
    assert _git(third, "show", f"{tip}:site/index.html") == "<title>Nerf Watch</title>"
    assert _git(third, "show", f"{tip}:site/assets/app.js") == "// app"
    assert _git(third, "rev-list", "--count", tip) == "2"
    # The code branch is untouched.
    assert _git(third, "ls-tree", "--name-only", "origin/main") == "README.md"


def test_push_refuses_to_overwrite_attempts_it_never_saw(origin, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@t")
    a = _clone(origin, tmp_path / "a")
    b = _clone(origin, tmp_path / "b")
    db_a, db_b = a / "regression.db", b / "regression.db"
    pull(a, db_a)
    pull(b, db_b)
    _db(db_a, "a")
    _db(db_b, "b")
    push(a, db_a)
    with pytest.raises(ResultsError, match="moved since pull"):
        push(b, db_b)


def test_push_requires_a_pull_and_pull_will_not_clobber_local_data(origin, tmp_path) -> None:
    clone = _clone(origin, tmp_path / "c")
    db = clone / "regression.db"
    db.write_bytes(b"local")
    with pytest.raises(ResultsError, match="pull"):
        push(clone, db)
    with pytest.raises(ResultsError, match="exists but"):
        pull(clone, db)
    assert db.read_bytes() == b"local"
