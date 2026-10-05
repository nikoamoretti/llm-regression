#!/usr/bin/env bash
set -euo pipefail
cd "${WORKSPACE:-/workspace}"
python3 "${GRADER:-/grader}/grade.py"
