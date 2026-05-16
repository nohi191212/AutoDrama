#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"

usage() {
  cat <<'USAGE'
Usage:
  scripts/run_pregen.sh [--config FILE] [--until NODE] [--force]

Default NODE:
  role_voice_design

This uses provider routing from config.yaml. For current config.yaml.example,
script/role text routing points to DeepSeek.
Project ID and input outline file are read from config.yaml. If project.id is
omitted, outputs/current_project.json is used.
USAGE
}

until="role_voice_design"
force=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      AUTODRAMA_CONFIG="${2:-}"
      shift 2
      ;;
    --until)
      until="${2:-}"
      shift 2
      ;;
    --force)
      force="--force"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

cd "${AUTODRAMA_ROOT}"
args=(run pregen --config "${AUTODRAMA_CONFIG}" --until "${until}")
if [[ -n "${force}" ]]; then
  args+=("${force}")
fi
autodrama_cli "${args[@]}"
