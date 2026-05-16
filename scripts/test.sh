#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"

mkdir -p /tmp/autodrama_pytest /tmp/autodrama_pytest_cache

cd "${AUTODRAMA_ROOT}/autodrama"
autodrama_python -m pytest \
  --basetemp=/tmp/autodrama_pytest \
  -o cache_dir=/tmp/autodrama_pytest_cache \
  "$@"
