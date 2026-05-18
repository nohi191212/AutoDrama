#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"

usage() {
  cat <<'USAGE'
Usage:
  scripts/run_pregen_fake.sh [--config FILE] [--project ID_OR_DIR] [--until NODE] [--force]

Default NODE:
  bgm_generation

This runs the pre-generation workflow only with fake providers.
Project ID and input outline file are read from config.yaml by default. Use
--project to run a specific existing project or output directory.
USAGE
}

until="bgm_generation"
force=""
project=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      AUTODRAMA_CONFIG="${2:-}"
      shift 2
      ;;
    --project)
      project="${2:-}"
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
args=(run pregen --config "${AUTODRAMA_CONFIG}" --until "${until}" --provider fake)
if [[ -n "${project}" ]]; then
  args+=(--project "${project}")
fi
if [[ -n "${force}" ]]; then
  args+=("${force}")
fi
autodrama_cli "${args[@]}"
