#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

config="${AUTODRAMA_CONFIG:-config.yaml}"
until="role_voice_design"
force=""
provider_script="scripts/run_pregen.sh"
blue=$'\033[34m'
reset=$'\033[0m'

log() {
  printf '%b[autodrama]%b %s\n' "${blue}" "${reset}" "$*"
}

usage() {
  cat <<'USAGE'
Usage:
  bash run/start.sh [--config FILE] [--fake] [--until NODE] [--force]

This is the config-driven one-command entry point:

  - If the configured project does not exist, create it from config.yaml.
  - If state.json already exists, resume/continue from that state.
  - Run the current MVP to role_voice_design.

Project ID, title, and script outline file must be configured in config.yaml.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      config="${2:-}"
      shift 2
      ;;
    --fake)
      provider_script="scripts/run_pregen_fake.sh"
      shift
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

log "config: ${config}"
log "running pregen until ${until}"
args=(--config "${config}" --until "${until}")
if [[ -n "${force}" ]]; then
  args+=("${force}")
fi
bash "${provider_script}" "${args[@]}"
