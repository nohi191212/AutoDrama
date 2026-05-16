#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"

PYTEST_TMP_DIR="${AUTODRAMA_ROOT}/.tmp/pytest"
PYTEST_CACHE_DIR="${AUTODRAMA_ROOT}/.tmp/pytest_cache"

mkdir -p "${PYTEST_TMP_DIR}" "${PYTEST_CACHE_DIR}"

cd "${AUTODRAMA_ROOT}/autodrama"
autodrama_python -m pytest \
  --basetemp="${PYTEST_TMP_DIR}" \
  -o "cache_dir=${PYTEST_CACHE_DIR}" \
  "$@"
