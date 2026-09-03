#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

detect_platform() {
  local uname_s
  uname_s="$(uname -s 2>/dev/null || echo unknown)"
  case "${uname_s}" in
    Darwin*)
      echo "macos"
      ;;
    MINGW*|MSYS*|CYGWIN*)
      echo "windows"
      ;;
    Linux*)
      echo "linux"
      ;;
    *)
      echo "unknown"
      ;;
  esac
}

config_from_args() {
  local previous=""
  for arg in "$@"; do
    if [[ "${previous}" == "--config" ]]; then
      printf '%s\n' "${arg}"
      return 0
    fi
    previous="${arg}"
  done

  if [[ -n "${AUTODRAMA_CONFIG:-}" ]]; then
    printf '%s\n' "${AUTODRAMA_CONFIG}"
  elif [[ -f "${ROOT_DIR}/config.yaml" ]]; then
    printf '%s\n' "${ROOT_DIR}/config.yaml"
  else
    printf '%s\n' "${ROOT_DIR}/config.yaml.example"
  fi
}

resolve_config_path() {
  local config="$1"
  if [[ "${config}" == /* || "${config}" == *:* ]]; then
    printf '%s\n' "${config}"
  else
    printf '%s\n' "${ROOT_DIR}/${config}"
  fi
}

yaml_runtime_python() {
  local config="$1"
  local platform="$2"
  local key=""
  local value=""
  local in_runtime="false"
  local in_python="false"

  while IFS= read -r raw_line || [[ -n "${raw_line}" ]]; do
    local line="${raw_line%%#*}"
    [[ -z "${line//[[:space:]]/}" ]] && continue

    if [[ "${line}" =~ ^[^[:space:]][^:]*: ]]; then
      key="${line%%:*}"
      if [[ "${key}" == "runtime" ]]; then
        in_runtime="true"
      else
        in_runtime="false"
      fi
      in_python="false"
      continue
    fi

    if [[ "${in_runtime}" == "true" && "${line}" =~ ^[[:space:]]{2}python: ]]; then
      in_python="true"
      continue
    fi

    if [[ "${in_runtime}" == "true" && "${in_python}" == "true" && "${line}" =~ ^[[:space:]]{2}[^[:space:]][^:]*: ]]; then
      in_python="false"
      continue
    fi

    if [[ "${in_runtime}" == "true" && "${in_python}" == "true" && "${line}" =~ ^[[:space:]]{4}([^:]+):[[:space:]]*(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      value="${BASH_REMATCH[2]}"
      value="${value%"${value##*[![:space:]]}"}"
      value="${value%\"}"
      value="${value#\"}"
      value="${value%\'}"
      value="${value#\'}"
      if [[ "${key}" == "${platform}" ]]; then
        printf '%s\n' "${value}"
        return 0
      fi
      if [[ "${key}" == "default" ]]; then
        local default_value="${value}"
      fi
    fi
  done < "${config}"

  printf '%s\n' "${default_value:-}"
}

command_path_or_literal() {
  local candidate="$1"
  if [[ -z "${candidate}" ]]; then
    return 1
  fi

  if [[ "${candidate}" == */* || "${candidate}" == *:* ]]; then
    printf '%s\n' "${candidate}"
    return 0
  fi

  if command -v "${candidate}" >/dev/null 2>&1; then
    command -v "${candidate}"
    return 0
  fi

  printf '%s\n' "${candidate}"
}

shell_path_for_windows() {
  local value="$1"
  if [[ "${AUTODRAMA_PLATFORM}" == "windows" && "${value}" == *:* && "$(command -v cygpath || true)" ]]; then
    cygpath -u "${value}"
  else
    printf '%s\n' "${value}"
  fi
}

python_arg_path() {
  local value="$1"
  if [[ "${AUTODRAMA_PLATFORM}" == "windows" && "$(command -v cygpath || true)" ]]; then
    local candidate="${value}"
    if [[ "${candidate}" != /* && "${candidate}" != *:* ]]; then
      candidate="${ROOT_DIR}/${candidate}"
    fi
    if [[ -e "${candidate}" ]]; then
      cygpath -w "${candidate}"
      return 0
    fi
  fi
  printf '%s\n' "${value}"
}

AUTODRAMA_PLATFORM="$(detect_platform)"
export AUTODRAMA_PLATFORM
export AUTODRAMA_ROOT="${ROOT_DIR}"

AUTODRAMA_CONFIG="$(resolve_config_path "$(config_from_args "$@")")"
export AUTODRAMA_CONFIG

if [[ -n "${AUTODRAMA_PYTHON:-}" ]]; then
  AUTODRAMA_PYTHON_SELECTED="${AUTODRAMA_PYTHON}"
else
  AUTODRAMA_PYTHON_SELECTED="$(yaml_runtime_python "${AUTODRAMA_CONFIG}" "${AUTODRAMA_PLATFORM}")"
  if [[ -z "${AUTODRAMA_PYTHON_SELECTED}" ]]; then
    AUTODRAMA_PYTHON_SELECTED="python3"
  fi
fi

AUTODRAMA_PYTHON="$(shell_path_for_windows "$(command_path_or_literal "${AUTODRAMA_PYTHON_SELECTED}")")"
export AUTODRAMA_PYTHON

PYTHONPATH_ENTRY="${ROOT_DIR}/autodrama/src"
PYTHONPATH_SEPARATOR=":"
if [[ "${AUTODRAMA_PLATFORM}" == "windows" && "$(command -v cygpath || true)" ]]; then
  PYTHONPATH_ENTRY="$(cygpath -w "${PYTHONPATH_ENTRY}")"
  PYTHONPATH_SEPARATOR=";"
fi
export PYTHONPATH="${PYTHONPATH_ENTRY}${PYTHONPATH:+${PYTHONPATH_SEPARATOR}${PYTHONPATH}}"

autodrama_python() {
  if [[ "${AUTODRAMA_PYTHON}" == */* || "${AUTODRAMA_PYTHON}" == *:* ]]; then
    if [[ ! -x "${AUTODRAMA_PYTHON}" && ! -f "${AUTODRAMA_PYTHON}" ]]; then
      cat >&2 <<EOF
AutoDrama Python not found:
  ${AUTODRAMA_PYTHON}

Detected platform:
  ${AUTODRAMA_PLATFORM}

Config file:
  ${AUTODRAMA_CONFIG}

Set runtime.python.${AUTODRAMA_PLATFORM} in config.yaml, or override:
  AUTODRAMA_PYTHON=/path/to/python bash run/start.sh --config config.yaml
EOF
      return 127
    fi
  elif ! command -v "${AUTODRAMA_PYTHON}" >/dev/null 2>&1; then
    cat >&2 <<EOF
AutoDrama Python command not found:
  ${AUTODRAMA_PYTHON}

Detected platform:
  ${AUTODRAMA_PLATFORM}

Config file:
  ${AUTODRAMA_CONFIG}
EOF
    return 127
  fi

  "${AUTODRAMA_PYTHON}" "$@"
}

autodrama_cli() {
  local args=()
  local convert_next="false"
  for arg in "$@"; do
    if [[ "${convert_next}" == "true" ]]; then
      args+=("$(python_arg_path "${arg}")")
      convert_next="false"
      continue
    fi

    args+=("${arg}")
    case "${arg}" in
      --config|--chapters-dir)
        convert_next="true"
        ;;
    esac
  done

  autodrama_python -m autodrama.cli "${args[@]}"
}
