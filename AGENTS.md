# Repository Agent Rules

## python environment
Python Environment: `D:/miniforge3/envs/autodrama/python.exe`

## Prompt Templates

- All prompt templates are sent directly to the LLM API, not to an Agent. When designing prompt templates, include only information the model needs to perform the requested content task.
- Do not add content-irrelevant project metadata merely for context, such as project title, project ID, source file path, episode key, episode count, or reference duration. Include such fields only when they directly affect the required output format, constraints, or reasoning.

## Known Permission / File Write Notes

- In this Codex desktop workspace, `apply_patch` may fail to create a new root-level file with only `Failed to write file ...`, even when the target is inside the writable repository and after explicit single-file write permission is granted. If this happens, retry `apply_patch` once after requesting explicit write permission for the target path. If it still fails and the user explicitly asked to create or update that file, use PowerShell `Set-Content -Encoding UTF8` as the fallback and immediately read the file back to verify the write.

## Local Windows / PowerShell Operations

- Treat the local environment as Windows with PowerShell. Do not assume that local `bash` is available: WSL or Git Bash may be missing or blocked by the sandbox. Use PowerShell-native commands for local work unless bash availability has been confirmed in the current session.
- Use `cmd /c` only as a fallback for simple inspection, copying, or directory listing. Avoid it for multiline edits, Unicode text generation, or exact patching.
- Prefer PowerShell-native commands such as `Get-Content`, `Copy-Item`, and `Move-Item` for lightweight local operations. Keep one shell end-to-end for filesystem changes rather than passing computed paths between PowerShell and another shell.
- PowerShell parses characters and expressions such as `<`, `>`, `|`, `${...}`, `$()`, brackets, and nested quotes before a child command sees them. Put complex shell or Python logic in a repository file and execute that file instead of embedding it in a long command line.
- Do not use Linux heredoc syntax such as `python - <<'PY'` in PowerShell. Follow this repository's smoke-script rules for any agent-written Python validation.
- Preserve UTF-8 without BOM and LF line endings for format-sensitive files such as Python, shell, Markdown, JSON, and patch files. Windows PowerShell 5.1 `Set-Content -Encoding UTF8` can add a BOM; when exact encoding matters, use an appropriate no-BOM writer and read the file back to verify it.
- Put task-generated temporary scripts, patches, logs, and copied files under the repository `.tmp/` directory. Clean up only files created by the current task, and do not bypass sandbox restrictions if cleanup is denied.
- If a local command fails, first distinguish among sandbox or filesystem permissions, PowerShell parsing, unavailable executables, child-process failures, and application-code failures before changing the implementation.
- Do not terminate existing local processes merely because they appear stuck. Report the process and symptoms, and use `Stop-Process`, `taskkill`, or equivalent only when the user explicitly authorizes termination.

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



