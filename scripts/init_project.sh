#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"

usage() {
  cat <<'USAGE'
Usage:
  scripts/init_project.sh --title TITLE --script-file FILE [--project-id ID]

Environment:
  AUTODRAMA_CONFIG   Defaults to config.yaml, then config.yaml.example.
  AUTODRAMA_PYTHON   Defaults to D:/miniforge3/envs/autodrama/python.exe.
USAGE
}

title=""
script_file=""
project_id=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --title)
      title="${2:-}"
      shift 2
      ;;
    --script-file)
      script_file="${2:-}"
      shift 2
      ;;
    --project-id)
      project_id="${2:-}"
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

if [[ -z "${title}" || -z "${script_file}" ]]; then
  usage >&2
  exit 2
fi

args=(init --config "${AUTODRAMA_CONFIG}" --title "${title}" --script-file "${script_file}")
if [[ -n "${project_id}" ]]; then
  args+=(--project-id "${project_id}")
fi

cd "${AUTODRAMA_ROOT}"
autodrama_cli "${args[@]}"
