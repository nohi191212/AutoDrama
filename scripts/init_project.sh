#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"

usage() {
  cat <<'USAGE'
Usage:
  scripts/init_project.sh [--config FILE]

Environment:
  AUTODRAMA_CONFIG   Defaults to config.yaml, then config.yaml.example.
  AUTODRAMA_PYTHON   Defaults to D:/miniforge3/envs/autodrama/python.exe.

Project title, project ID, and input outline file are read from config.yaml:

  project.id
  project.title
  project.script_outline_file
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      AUTODRAMA_CONFIG="${2:-}"
      shift 2
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

args=(init --config "${AUTODRAMA_CONFIG}")

cd "${AUTODRAMA_ROOT}"
autodrama_cli "${args[@]}"
