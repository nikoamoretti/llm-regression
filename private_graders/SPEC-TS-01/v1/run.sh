#!/usr/bin/env bash
set -euo pipefail
cd "${WORKSPACE:-/workspace}"
node "${GRADER:-/grader}/grade.mjs"
