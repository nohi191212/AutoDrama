#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

AUTODRAMA_PYTHON="${AUTODRAMA_PYTHON:-D:/miniforge3/envs/autodrama/python.exe}"

if [[ "${AUTODRAMA_PYTHON}" == *:* ]]; then
  if command -v cygpath >/dev/null 2>&1; then
    AUTODRAMA_PYTHON="$(cygpath -u "${AUTODRAMA_PYTHON}")"
  else
    drive="${AUTODRAMA_PYTHON:0:1}"
    rest="${AUTODRAMA_PYTHON:2}"
    drive="$(printf '%s' "${drive}" | tr '[:upper:]' '[:lower:]')"
    AUTODRAMA_PYTHON="/${drive}${rest}"
  fi
fi

export PYTHONPATH="${ROOT_DIR}/autodrama/src${PYTHONPATH:+:${PYTHONPATH}}"
export AUTODRAMA_ROOT="${ROOT_DIR}"
export AUTODRAMA_CONFIG="${AUTODRAMA_CONFIG:-${ROOT_DIR}/config.yaml}"

if [[ ! -f "${AUTODRAMA_CONFIG}" ]]; then
  AUTODRAMA_CONFIG="${ROOT_DIR}/config.yaml.example"
fi

autodrama_python() {
  "${AUTODRAMA_PYTHON}" "$@"
}

autodrama_cli() {
  autodrama_python -m autodrama.cli "$@"
}
