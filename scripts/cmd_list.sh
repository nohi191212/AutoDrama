#!/usr/bin/env bash
# AutoDrama single-node command list and safe dispatcher.
#
# Print every copyable command:
#   bash scripts/cmd_list.sh --list
#
# Run exactly one node:
#   bash scripts/cmd_list.sh <pregen|generation|postgen> <node> <project_id>
#
# Start with a new project ID for script_import.  After a node completes,
# reuse that same project ID for the next node.  This script never
# supplies --force, --until, --provider, --clips, --shots, or --assets.

set -euo pipefail

PYTHON_EXE='D:/miniforge3/envs/autodrama/python.exe'
CONFIG_PATH='C:/Users/csh10/Desktop/projects/202605_AIGC/AutoDrama/config.saodi_bashinian_terra_image2.yaml'
OUTPUT_ROOT='C:/Users/csh10/Desktop/projects/202605_AIGC/AutoDrama/outputs'

# Default pregen chain for the current mature-script workflow.  Image-audit
# nodes remain listed because the code treats them as required delivery gates.
pregen_default_nodes=(
  script_import
  script_cinematic_adapt
  script_novel_extract
  script_worldview_extract
  key_vision_prompt
  key_vision_image_generation
  key_vision_image_audit
  role_extract_primary
  role_extract_functional
  role_finalize
  roleboard_prompt
  roleboard_image_generation
  roleboard_image_audit
  prop_extract
  prop_finalize
  layout_extract
  layout_finalize
  prop_prompt
  layout_prompt
  prop_image_generation
  prop_image_audit
  layout_image_generation
  layout_image_audit
  clip_segment
  clip_to_shots
  layout_to_background_prompt
  shot_background_shot_reference
  shot_background_image_generation
  shot_background_image_audit
  shot_keyframe_prompt
  shot_keyframe_image_generation
  shot_keyframe_image_audit
  shot_manifest_generation
)

# Accepted pregen --only nodes that are deferred, alternate-path, or disabled
# by the current YAML's optional branches.  Run them only when deliberately
# enabled or required by the selected workflow path.
pregen_optional_nodes=(
  key_vision_edit
  script_outline
  script_novel
  layout_prop_boundary_review
  role_subject_frontal_image_generation
  role_subject_frontal_image_audit
  role_voice_select
  role_kling_voice_generation
  role_subject_video_generation
  role_subject_element_generation
  bgm_design
  bgm_generation
)

generation_nodes=(
  shot_dialogue_audio_generation
  shot_video_generation
  shot_video_audit
  dynamic_asset_solidification
)

postgen_nodes=(
  postgen_source_collect
  postgen_source_asr
  postgen_source_audit
  postgen_edit_plan_generation
  postgen_edit_plan_validation
  postgen_video_composition
  postgen_audio_separation
  postgen_speaker_diarization
  postgen_voice_conversion
  postgen_audio_remix
  postgen_subtitle_asr
  postgen_subtitle_render
  postgen_final_audit
)

contains_node() {
  local wanted="$1"
  shift
  local candidate
  for candidate in "$@"; do
    [[ "$candidate" == "$wanted" ]] && return 0
  done
  return 1
}

print_group() {
  local workflow="$1"
  local title="$2"
  shift 2
  local node

  printf '\n# %s\n' "$title"
  for node in "$@"; do
    printf '"%s" -m autodrama.cli run %s --config "%s" --project "<PROJECT_ID>" --only %s\n' \
      "$PYTHON_EXE" "$workflow" "$CONFIG_PATH" "$node"
  done
}

print_commands() {
  cat <<'EOF'
# Use a new <PROJECT_ID> for the first script_import command.  Do not run more
# than one command before examining that node's state, JSON, prompt, and logs.
# Commands below use the configured real providers; no fake-provider override
# or explicit scope selector is included.
EOF
  print_group pregen 'Pregen default chain' "${pregen_default_nodes[@]}"
  print_group pregen 'Pregen optional/deferred nodes' "${pregen_optional_nodes[@]}"
  printf '\n# Pregen node groups\n'
  printf '"%s" -m autodrama.cli run pregen --config "%s" --project "<PROJECT_ID>" --node-group key_vision\n' \
    "$PYTHON_EXE" "$CONFIG_PATH"
  printf '"%s" -m autodrama.cli run pregen --config "%s" --project "<PROJECT_ID>" --node-group key_vision_edit\n' \
    "$PYTHON_EXE" "$CONFIG_PATH"
  printf '"%s" -m autodrama.cli run pregen --config "%s" --project "<PROJECT_ID>" --node-group role_extract\n' \
    "$PYTHON_EXE" "$CONFIG_PATH"
  print_group generation 'Generation chain' "${generation_nodes[@]}"
  print_group postgen 'Postgen chain' "${postgen_nodes[@]}"
}

usage() {
  cat <<'EOF'
Usage:
  bash scripts/cmd_list.sh --list
  bash scripts/cmd_list.sh <pregen|generation|postgen> <node> <project_id>

The dispatcher validates the node name and invokes exactly one --only command.
EOF
}

if [[ $# -eq 0 || "${1:-}" == '--list' ]]; then
  print_commands
  exit 0
fi

if [[ $# -ne 3 ]]; then
  usage >&2
  exit 2
fi

workflow="$1"
node="$2"
project_id="$3"

case "$workflow" in
  pregen)
    if ! contains_node "$node" "${pregen_default_nodes[@]}" && \
       ! contains_node "$node" "${pregen_optional_nodes[@]}"; then
      printf 'Unsupported pregen node: %s\n' "$node" >&2
      exit 2
    fi
    ;;
  generation)
    if ! contains_node "$node" "${generation_nodes[@]}"; then
      printf 'Unsupported generation node: %s\n' "$node" >&2
      exit 2
    fi
    ;;
  postgen)
    if ! contains_node "$node" "${postgen_nodes[@]}"; then
      printf 'Unsupported postgen node: %s\n' "$node" >&2
      exit 2
    fi
    ;;
  *)
    printf 'Unsupported workflow: %s\n' "$workflow" >&2
    usage >&2
    exit 2
    ;;
esac

if [[ "$workflow" == 'pregen' && "$node" == 'script_import' && -e "$OUTPUT_ROOT/$project_id" ]]; then
  printf 'Refusing script_import because its project directory already exists: %s/%s\n' \
    "$OUTPUT_ROOT" "$project_id" >&2
  printf 'Choose a new project ID; this dispatcher never adds --force.\n' >&2
  exit 2
fi

exec "$PYTHON_EXE" -m autodrama.cli run "$workflow" \
  --config "$CONFIG_PATH" \
  --project "$project_id" \
  --only "$node"
