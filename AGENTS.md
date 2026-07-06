# Repository Agent Rules

## python environment
Python Environment: `D:/miniforge3/envs/autodrama/python.exe`

## Prompt Templates

- All prompt templates are sent directly to the LLM API, not to an Agent. When designing prompt templates, include only information the model needs to perform the requested content task.
- Do not add content-irrelevant project metadata merely for context, such as project title, project ID, source file path, episode key, episode count, or reference duration. Include such fields only when they directly affect the required output format, constraints, or reasoning.
## Verification

- Do not use `pytest` for validation in this repository.
- Do not run `python -m pytest`, `pytest`, or any project/script command whose primary purpose is invoking pytest.
- Prefer non-pytest checks such as:
  - `python -m compileall`
  - focused smoke scripts
  - direct CLI runs against the relevant workflow
  - static inspection of generated JSON/log files
  - small targeted Python files that exercise the changed code path without pytest
- Do not run inline Python through `python -`, heredocs, or console-fed scripts for validation.
- Put agent-written Python verification/smoke files under `scripts/smoke/`, then run the file path explicitly.
- Smoke tests and agent-written verification scripts should write temporary outputs under the repository `.tmp/` directory, not `C:/tmp` or other system temp directories.
- If a user explicitly asks for tests, ask what non-pytest verification they want before running anything pytest-related.
