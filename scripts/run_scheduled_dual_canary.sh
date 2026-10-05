#!/usr/bin/env bash
# Trusted local dual-model canary. Never a GitHub PR secret job.
# Never locks or changes a scientific baseline.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LOCK="${ROOT}/artifacts/scheduled-dual-canary.lock"
mkdir -p "${ROOT}/artifacts"

if [[ -e "$LOCK" ]]; then
  echo "another scheduled dual canary is running: $LOCK" >&2
  exit 2
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

if [[ -n "$(git status --porcelain)" ]]; then
  echo "harness git worktree is dirty; refusing to run" >&2
  exit 3
fi

PYTHON="${PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON=python3
  elif command -v python >/dev/null 2>&1; then
    PYTHON=python
  else
    echo "python3/python not found" >&2
    exit 1
  fi
fi

"$PYTHON" -m runner doctor --live --models grok-4.6,gpt-6-astra --tracks model_only,agentic --effort xhigh
"$PYTHON" -m runner evaluate \
  --suite canary \
  --models grok-4.6,gpt-6-astra \
  --efforts low,medium,high,xhigh \
  --tracks model_only,agentic \
  --repeats 1 \
  --schedule-seed 20260911 \
  --blocked-randomization \
  --fresh \
  --trigger scheduled-dual-canary
"$PYTHON" -m runner dashboard build

echo "scheduled dual canary finished; baseline was not changed"
