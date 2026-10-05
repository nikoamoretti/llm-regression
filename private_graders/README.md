# Hidden graders

A task passes only if hidden tests the agent never sees pass. If those tests and the gold patches
sat in this public repository, future models could be trained on them, so they are kept out:

- **Frozen benchmark tasks** (`BUG-PY-01` … `LONG-01`): their graders live in the private
  repository `nikoamoretti/llm-regression-archive`. `scripts/fetch_graders.sh` copies them here,
  and `python -m runner.verify_hashes` checks them against the hashes frozen in each `task.yaml`.
- **Mined tasks** (`MINE-*`): `python -m runner mine --batch configs/mined.yaml` rebuilds their
  graders from the private source repositories.
- **Demo tasks** (`DEMO-*`): their graders are committed here on purpose. They are in no benchmark
  suite; CI and the unit tests use them.

Everything else in this directory is gitignored. Never commit a benchmark task's graders.

Without the hidden graders, `runner evaluate` refuses to run a task, and `runner.validate_tasks`
and `runner.verify_hashes` fail unless given `--allow-missing-graders` (public CI only).
