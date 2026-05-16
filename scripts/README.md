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
AUTODRAMA_CONFIG=/path/to/config.yaml scripts/run_pregen_fake.sh --project demo
```

## Common Commands

Initialize a project:

```bash
scripts/init_project.sh \
  --title "测试短片" \
  --script-file "短剧生成方案.md" \
  --project-id review_demo
```

Run the current MVP with fake provider, stopping at `role_voice_design`:

```bash
scripts/run_pregen_fake.sh --project review_demo
```

Run with configured providers:

```bash
scripts/run_pregen.sh --project review_demo
```

Inspect state:

```bash
scripts/inspect_state.sh review_demo
```

List node JSON outputs:

```bash
scripts/inspect_nodes.sh review_demo
```

Run tests:

```bash
scripts/test.sh
```
