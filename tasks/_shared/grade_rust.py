#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKSPACE = Path(os.environ.get("WORKSPACE", "/workspace"))


def run_cargo(manifest: Path) -> dict:
    proc = subprocess.run(
        ["cargo", "test", "--offline", "--manifest-path", str(manifest), "--", "--nocapture"],
        cwd=manifest.parent,
        text=True,
        capture_output=True,
    )
    # First run may need network if no cargo cache; retry without --offline.
    if proc.returncode != 0 and "--offline" in (proc.stderr + proc.stdout):
        proc = subprocess.run(
            ["cargo", "test", "--manifest-path", str(manifest), "--", "--nocapture"],
            cwd=manifest.parent,
            text=True,
            capture_output=True,
        )
    ok = proc.returncode == 0
    log = (proc.stdout + proc.stderr)[-4000:]
    return {"ok": ok, "log": log}


def main() -> int:
    manifest = ROOT / "check" / "Cargo.toml"
    text = manifest.read_text(encoding="utf-8")
    manifest.write_text(text.replace("__WORKSPACE__", str(WORKSPACE)), encoding="utf-8")
    result = run_cargo(manifest)
    payload = {
        "functional": {"passed": 1 if result["ok"] else 0, "total": 1},
        "regression": {"passed": 1 if result["ok"] else 0, "total": 1},
        "constraints": {"passed": 1, "total": 1},
        "forbidden_changes": [],
        "strict_pass": result["ok"],
        "tests": [
            {
                "id": "cargo/check",
                "group": "functional",
                "passed": result["ok"],
                "failure_signature": None if result["ok"] else result["log"],
            }
        ],
        "logs": {"cargo": result["log"]},
    }
    encoded = json.dumps(payload)
    print(encoded)
    (WORKSPACE / ".grade.json").write_text(encoded, encoding="utf-8")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
