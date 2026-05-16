#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      AUTODRAMA_CONFIG="${2:-}"
      shift 2
      ;;
    -h|--help)
      cat <<'USAGE'
Usage:
  scripts/inspect_state.sh [--config FILE]
USAGE
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

cd "${AUTODRAMA_ROOT}"
args=(inspect state --config "${AUTODRAMA_CONFIG}")
autodrama_cli "${args[@]}"
