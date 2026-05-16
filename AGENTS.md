# Repository Agent Rules

## python environment
Python Environment: `D:/miniforge3/envs/autodrama/python.exe`

## Verification

- Do not use `pytest` for validation in this repository.
- Do not run `python -m pytest`, `pytest`, or any project/script command whose primary purpose is invoking pytest.
- Prefer non-pytest checks such as:
  - `python -m compileall`
  - focused smoke scripts
  - direct CLI runs against the relevant workflow
  - static inspection of generated JSON/log files
  - small targeted Python commands that exercise the changed code path without pytest
- If a user explicitly asks for tests, ask what non-pytest verification they want before running anything pytest-related.
