#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKSPACE = Path(os.environ.get("WORKSPACE", "/workspace"))


def write_mod(directory: Path) -> None:
    (directory / "go.mod").write_text(
        "\n".join(
            [
                "module gradercheck",
                "go 1.22",
                "require example.com/state v0.0.0",
                f"replace example.com/state => {WORKSPACE}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def run_group(name: str) -> dict:
    directory = ROOT / "check" / name
    if not directory.exists():
        return {"passed": 0, "total": 0, "tests": []}
    write_mod(directory)
    proc = subprocess.run(
        ["go", "test", "-count=1", "."],
        cwd=directory,
        text=True,
        capture_output=True,
        env={**os.environ, "GOFLAGS": "-mod=mod"},
    )
    ok = proc.returncode == 0
    log = (proc.stdout + proc.stderr)[-4000:]
    return {
        "passed": 1 if ok else 0,
        "total": 1,
        "tests": [
            {
                "id": f"go/{name}",
                "group": name,
                "passed": ok,
                "failure_signature": None if ok else log,
            }
        ],
        "log": log,
    }


def main() -> int:
    functional = run_group("functional")
    regression = run_group("regression")
    payload = {
        "functional": {"passed": functional["passed"], "total": functional["total"]},
        "regression": {"passed": regression["passed"], "total": regression["total"]},
        "constraints": {"passed": 1, "total": 1},
        "forbidden_changes": [],
        "strict_pass": functional["passed"] == functional["total"] and functional["total"] > 0
        and regression["passed"] == regression["total"],
        "tests": functional["tests"] + regression["tests"],
        "logs": {"functional": functional.get("log"), "regression": regression.get("log")},
    }
    text = json.dumps(payload)
    print(text)
    (WORKSPACE / ".grade.json").write_text(text, encoding="utf-8")
    return 0 if payload["strict_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
