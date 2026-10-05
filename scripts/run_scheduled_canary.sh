#!/usr/bin/env bash
# Optional unblended Codex / GPT-5.6 Sol canary.
# Never mix these rows into Grok/Astra series. Not a frozen baseline.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LOCK="${ROOT}/artifacts/scheduled-canary.lock"
mkdir -p "${ROOT}/artifacts"

if [[ -e "$LOCK" ]]; then
  echo "another scheduled canary is running: $LOCK" >&2
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

"$PYTHON" -m runner doctor --track codex_product --model gpt-5.6-sol --effort max
"$PYTHON" -m runner evaluate \
  --suite canary \
  --track codex_product \
  --model gpt-5.6-sol \
  --efforts max \
  --repeats 3 \
  --fresh \
  --trigger scheduled-canary
"$PYTHON" -m runner dashboard build

echo "scheduled canary finished; baseline was not changed"
