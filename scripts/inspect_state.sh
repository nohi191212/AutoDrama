#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" || $# -lt 1 ]]; then
  cat <<'USAGE'
Usage:
  scripts/inspect_state.sh PROJECT_ID
USAGE
  exit $([[ $# -lt 1 ]] && echo 2 || echo 0)
fi

cd "${AUTODRAMA_ROOT}"
autodrama_cli inspect state --config "${AUTODRAMA_CONFIG}" --project "$1"
