#!/usr/bin/env bash
# Copy the benchmark tasks' hidden graders into private_graders/ from the private archive repository.
#
# The public repository holds every task's prompt, fixture and manifest, but only the demo tasks'
# graders. The frozen tasks' graders live in the private nikoamoretti/llm-regression-archive
# (archived, so read-only). Mined tasks are not fetched: `runner mine` rebuilds theirs from the
# private source repositories. The manifests' grader hashes catch any copy that does not match:
# run `python -m runner.verify_hashes` afterwards (scripts/cloud_run.sh does).
set -euo pipefail
cd "$(dirname "$0")/.."

SOURCE=${GRADERS_REPO:-https://github.com/nikoamoretti/llm-regression-archive}
DIR=${GRADERS_DIR:-${REPOS_DIR:-/home/user}/llm-regression-archive}

if [ ! -d "$DIR/.git" ]; then
  git clone -q --depth 1 "$SOURCE" "$DIR"
fi

copied=0
for version_dir in tasks/*/v*/; do
  id=$(basename "$(dirname "$version_dir")")
  version=$(basename "$version_dir")
  case "$id" in MINE-*|DEMO-*) continue ;; esac
  src="$DIR/private_graders/$id/$version"
  dest="private_graders/$id/$version"
  if [ ! -d "$src" ]; then
    echo "no hidden graders for $id/$version in $DIR" >&2
    exit 1
  fi
  rm -rf "$dest"
  mkdir -p "$(dirname "$dest")"
  cp -a "$src" "$dest"
  copied=$((copied + 1))
done
echo "fetched hidden graders for $copied task versions from $DIR"
