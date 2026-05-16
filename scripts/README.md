# Scripts

These Bash scripts wrap common AutoDrama commands.

Default Python:

```bash
D:/miniforge3/envs/autodrama/python.exe
```

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

Run the current MVP with fake provider, stopping at `role_voice_design`:

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

One-command fake run for review:

```bash
bash run/start.sh --config config.yaml --fake --force
```

Inspect state:

```bash
scripts/inspect_state.sh --config config.yaml
```

List node JSON outputs:

```bash
scripts/inspect_nodes.sh --config config.yaml
```

Run tests:

```bash
scripts/test.sh
```
