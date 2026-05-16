#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"

cd "${AUTODRAMA_ROOT}/autodrama"
autodrama_python -m compileall src tests "$@"
