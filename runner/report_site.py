"""Build the results site: a static report the daily run publishes to the ``results`` branch.

Writes the page (``runner/site/``) and its data (``data.json``, from
``runner.product_dashboard.collect``) into one directory. ``results push``
stores that directory as ``site/`` on the ``results`` branch, and the hosting
project deploys it from there, so the site is always the latest pushed run.

    python -m runner site [--out artifacts/site]
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from runner.product_dashboard import collect

ASSETS = Path(__file__).with_name("site")
# When the daily routine fires (UTC); shown as the next run on the page.
SCHEDULE_UTC = (8, 52)


def next_run(now: datetime) -> datetime:
    run = now.replace(hour=SCHEDULE_UTC[0], minute=SCHEDULE_UTC[1], second=0, microsecond=0)
    return run if run > now else run + timedelta(days=1)


def build(out: Path, data: dict[str, Any], *, now: datetime | None = None) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    for asset in sorted(ASSETS.iterdir()):
        if asset.is_file():
            shutil.copyfile(asset, out / asset.name)
    stamp = now or datetime.now(timezone.utc)
    payload = {**data, "next_run": next_run(stamp).isoformat(timespec="minutes")}
    (out / "data.json").write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="runner site", description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", type=Path, default=Path("artifacts/regression.db"))
    parser.add_argument("--monitor-json", type=Path, default=Path("artifacts/monitor/monitor.json"))
    parser.add_argument("--out", type=Path, default=Path("artifacts/site"))
    args = parser.parse_args(argv)
    data = collect(args.db, args.monitor_json)
    build(args.out, data)
    print(f"wrote {args.out}/ ({len(data['runs'])} runs, {len(data['attempts'])} attempts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
