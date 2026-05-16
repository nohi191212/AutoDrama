# Scripts

These Bash scripts wrap common AutoDrama commands.

Default Python:

```bash
runtime.python.<platform> in config.yaml
```

Supported platform keys:

- `windows`
- `macos`
- `linux`
- `default`

Override when needed:

```bash
AUTODRAMA_PYTHON=/path/to/python scripts/test.sh
```

Default config resolution:

1. `config.yaml`
2. `config.yaml.example`

Override when needed:

```bash
scripts/run_pregen_fake.sh --config /path/to/config.yaml
```

## Common Commands

Initialize a project:

```bash
scripts/init_project.sh --config config.yaml
```

This explicitly creates or rewrites the configured project state. For normal
resume-style execution, prefer `run/start.sh` or `scripts/run_pregen*.sh`.

Run the current MVP with fake provider, stopping at `role_voice_generation`:

```bash
scripts/run_pregen_fake.sh --config config.yaml
```

Run with configured providers:

```bash
scripts/run_pregen.sh --config config.yaml
```

One-command start from config:

```bash
bash run/start.sh --config config.yaml
```

`run/start.sh` does not call `init_project.sh` unconditionally. It lets the CLI
create the project if missing, or resume from `state.json` when it already
exists.

Native Windows start with the configured Windows Python:

```bat
run\start.cmd --config config.yaml
```

Use this when you want to run with `D:/miniforge3/envs/autodrama/python.exe`.
Do not use WSL for that mode; WSL is treated as Linux by `env.sh`.

One-command fake run for review:

```bash
bash run/start.sh --config config.yaml --fake --force
```

Native Windows fake run:

```bat
run\start.cmd --config config.yaml --fake --force
```

Inspect state:

```bash
scripts/inspect_state.sh --config config.yaml
```

List node JSON outputs:

```bash
scripts/inspect_nodes.sh --config config.yaml
```

Run non-pytest verification:

```bash
scripts/test.sh
```
