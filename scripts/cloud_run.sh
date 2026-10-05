#!/usr/bin/env bash
# One scheduled run of the product series inside a Claude Code cloud session
# (fresh machine each time). See README, "Scheduled cloud runs".
#
#   scripts/cloud_run.sh [--trials N] [--suite NAME] [--efforts LIST] [--rotation N]
#
# Needs CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) as an environment
# variable. With CURSOR_API_KEY set too, the Cursor series (Grok 4.7, suite
# $CURSOR_SUITE) runs the same tasks the same day; without it, it is skipped.
# The CA bundle trusts the sandbox's TLS-inspecting gateway; certificate
# verification stays on.
set -euo pipefail
cd "$(dirname "$0")/.."

SUITE=product
CURSOR_SUITE=${CURSOR_SUITE:-product-grok}
TRIALS=1
EFFORTS=high
WORKERS=${WORKERS:-4}
ROTATION=${ROTATION:-4}
while [ $# -gt 0 ]; do
  case "$1" in
    --suite) SUITE=$2; shift 2 ;;
    --trials) TRIALS=$2; shift 2 ;;
    --efforts) EFFORTS=$2; shift 2 ;;
    --rotation) ROTATION=$2; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

: "${CLAUDE_CODE_OAUTH_TOKEN:?add CLAUDE_CODE_OAUTH_TOKEN to the cloud environment variables}"
REPOS_DIR=${REPOS_DIR:-/home/user}
CA=${LLMREG_EXTRA_CA:-/root/.ccr/ca-bundle.crt}
VENV=${LLMREG_VENV:-/tmp/llmreg-venv}
LOG_DIR=${LOG_DIR:-artifacts/cloud}
RESULTS_REMOTE=${RESULTS_REMOTE:-origin}
mkdir -p "$LOG_DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)

step() { echo "== $(date -u +%H:%M:%S) $*"; }

step "docker"
if ! docker info >/dev/null 2>&1; then
  (dockerd >"$LOG_DIR/dockerd.log" 2>&1 &)
  for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 1; done
  docker info >/dev/null
fi

step "harness"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --disable-pip-version-check -e ".[dev]"
fi
export PATH="$VENV/bin:$PATH"

step "source repositories"
for repo in $(python - <<'PY'
import yaml
print(" ".join(sorted({t["repo"] for t in yaml.safe_load(open("configs/mined.yaml"))["tasks"]})))
PY
); do
  if [ ! -d "$REPOS_DIR/$repo/.git" ]; then
    git clone -q "https://github.com/nikoamoretti/$repo" "$REPOS_DIR/$repo"
  elif [ -f "$REPOS_DIR/$repo/.git/shallow" ]; then
    git -C "$REPOS_DIR/$repo" fetch -q --unshallow
  fi
done

BUILD=(--build-network host --extra-ca "$CA")
step "images"
python -m runner images build --no-cursor "${BUILD[@]}" >"$LOG_DIR/images-$STAMP.log" 2>&1
CURSOR=0
if [ -n "${CURSOR_API_KEY:-}" ]; then
  # The Cursor series is optional: a failed image build skips it, never the Claude series.
  if python -m runner images build --no-claude-code "${BUILD[@]}" >>"$LOG_DIR/images-$STAMP.log" 2>&1; then
    CURSOR=1
  else
    echo "cursor image build failed: the Cursor series is skipped today (see $LOG_DIR/images-$STAMP.log)"
  fi
fi
step "mined tasks"
python -m runner mine --batch configs/mined.yaml --repos-dir "$REPOS_DIR" "${BUILD[@]}" \
  >"$LOG_DIR/mine-$STAMP.log" 2>&1 || { tail -20 "$LOG_DIR/mine-$STAMP.log"; exit 1; }
tail -n 40 "$LOG_DIR/mine-$STAMP.log" | grep -E '^MINE-' || true
python -m runner images build-env "${BUILD[@]}" >"$LOG_DIR/env-$STAMP.log" 2>&1

step "results pull"
python -m runner results pull --remote "$RESULTS_REMOTE"

# A rotating slice keeps the daily cost low: every task runs once every $ROTATION days
# and each composite hard task once a week.
TASKS=$(python -m runner rotation --suite "$SUITE" --slots "$ROTATION")
START=$(date -u +%Y-%m-%dT%H:%M:%S)
step "evaluate $SUITE trials=$TRIALS efforts=$EFFORTS rotation=1/$ROTATION ($TASKS)"
set +e
LLMREG_EXTRA_CA="$CA" python -m runner evaluate --suite "$SUITE" --efforts "$EFFORTS" \
  --repeats "$TRIALS" --workers "$WORKERS" --tasks "$TASKS" --trigger schedule 2>&1 | tee "$LOG_DIR/evaluate-$STAMP.log"
EVAL=${PIPESTATUS[0]}
CURSOR_EVAL=-1
if [ "$CURSOR" -eq 1 ]; then
  # Same tasks, same day: the two products are compared on identical work.
  step "evaluate $CURSOR_SUITE (Cursor) trials=$TRIALS efforts=$EFFORTS ($TASKS)"
  LLMREG_EXTRA_CA="$CA" python -m runner evaluate --suite "$CURSOR_SUITE" --efforts "$EFFORTS" \
    --repeats "$TRIALS" --workers "$WORKERS" --tasks "$TASKS" --trigger schedule 2>&1 \
    | tee "$LOG_DIR/evaluate-cursor-$STAMP.log"
  CURSOR_EVAL=${PIPESTATUS[0]}
fi
echo "== $(date -u +%H:%M:%S) behaviour (transcript metrics)"
python -m runner behavior --since "$START"
echo "== $(date -u +%H:%M:%S) judge (quality and clarity rubric)"
# Rubric scores are a separate measurement; a judge failure never changes strict results.
LLMREG_EXTRA_CA="$CA" python -m runner judge --since "$START" 2>&1 | tee "$LOG_DIR/judge-$STAMP.log"
python -m runner monitor | tee "$LOG_DIR/monitor-$STAMP.log"
MONITOR=${PIPESTATUS[0]}
python -m runner site
set -e

step "results push"
SUMMARY=$(python - "$LOG_DIR/evaluate-$STAMP.log" "$SUITE" "$TRIALS" "$EFFORTS" "$EVAL" "$MONITOR" \
  "$LOG_DIR/evaluate-cursor-$STAMP.log" "$CURSOR_EVAL" <<'PY'
import collections, json, os, re, sys
log, suite, trials, efforts, ev, mon, cursor_log, cursor_ev = sys.argv[1:]
def statuses(path):
    return collections.Counter(re.findall(r"status=(\w+)", open(path).read())) if os.path.exists(path) else {}
summary = {"suite": suite, "trials": int(trials), "efforts": efforts, "statuses": statuses(log),
           "evaluate_exit": int(ev), "monitor_exit": int(mon)}
if int(cursor_ev) >= 0:
    summary["cursor"] = {"statuses": statuses(cursor_log), "evaluate_exit": int(cursor_ev)}
print(json.dumps(summary))
PY
)
python -m runner results push --remote "$RESULTS_REMOTE" --summary "$SUMMARY"
echo "$SUMMARY"
[ "$MONITOR" -eq 1 ] && echo "DRIFT ALARM: see the monitor report on the results branch"
[ "$EVAL" -ne 0 ] && exit "$EVAL"
# Nothing graded (e.g. an expired token: every attempt infra_fail/auth) is a failed run.
python - "$SUMMARY" <<'PY' || exit 3
import json, sys
statuses = json.loads(sys.argv[1])["statuses"]
graded = statuses.get("quality_pass", 0) + statuses.get("quality_fail", 0)
if not graded:
    print("NO ATTEMPTS GRADED: check CLAUDE_CODE_OAUTH_TOKEN and the evaluate log", file=sys.stderr)
    sys.exit(1)
PY
