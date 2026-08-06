# Named visual styles

Each `*.md` file in this directory is a reusable global visual-style prompt. Its body is plain prompt
content with no schema, headings or field structure. The filename without the extension is the
configuration name:

```yaml
generation:
  visual_style: xuanhuan-v2
```

Preset names use lowercase letters, digits, hyphens and underscores. The loader reads the selected
Markdown body verbatim. Keep story facts, character identity, laterality, blocking, scene-specific
continuity, shot design, palette and motivated scene light out of these global prompts; Gemini designs
the shot contract and scene-style contract for each key vision.
