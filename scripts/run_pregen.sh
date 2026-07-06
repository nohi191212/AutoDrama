#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"

usage() {
  cat <<'USAGE'
Usage:
  scripts/run_pregen.sh [--config FILE] [--project ID_OR_DIR] [--until NODE] [--only NODE] [--episodes 1,3] [--force]

Default NODE:
  clip_manifest_generation

This uses provider routing from config.yaml. For current config.yaml.example,
Aliyun/DashScope capabilities are configured under providers.aliyun and routed
per capability; role text still points to DeepSeek by default.
Project ID and input outline file are read from config.yaml by default. Use
--project to run a specific existing project or output directory. --episodes is
supported for clip_segment, roleboard_prompt, roleboard_image_generation, clip_prompt,
clip_storyboard_prompt, clip_storyboard_image_generation, clip_manifest_generation, role_voice_select,
prop_prompt, prop_image_generation, and layout_image_generation
when used with --only. Ambient entity, role subject, voice, and BGM nodes are
deferred from the default pregen chain and can be run manually with --only.
USAGE
}

until="clip_manifest_generation"
force=""
project=""
only=""
episodes=""

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
    --only|--node)
      only="${2:-}"
      shift 2
      ;;
    --episodes|--episode)
      episodes="${2:-}"
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
if [[ -n "${project}" ]]; then
  args+=(--project "${project}")
fi
if [[ -n "${only}" ]]; then
  args+=(--only "${only}")
fi
if [[ -n "${episodes}" ]]; then
  args+=(--episodes "${episodes}")
fi
if [[ -n "${force}" ]]; then
  args+=("${force}")
fi
autodrama_cli "${args[@]}"
