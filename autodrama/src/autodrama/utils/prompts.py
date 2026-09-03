"""Directory-based prompt rendering and final-prompt audit support.

Prompt templates describe only the content task.  Any deterministic assembly
needed after a model returns content lives beside that template in ``render.py``.
The audit writer records the exact string handed to a provider, never a request
payload or media reference.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any


_PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")
_VARIABLE_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_UNSAFE_FILE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _normalise_text(value: str) -> str:
    """Use stable LF line endings without changing the prompt's paragraphs."""
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _template_variables(template: str, *, label: str) -> set[str]:
    """Return declared template variables and reject unsupported placeholders.

    A typo such as ``{{shot.id}}`` used to be left in the request because the
    old matcher only recognised valid identifiers.  Templates deliberately
    support a small, explicit variable language, so every double-brace token
    must be one of those identifiers.
    """
    variables: set[str] = set()
    for match in _PLACEHOLDER_PATTERN.finditer(template):
        variable = match.group(1).strip()
        if not _VARIABLE_PATTERN.fullmatch(variable):
            raise ValueError(f"Prompt template {label} has invalid variable: {variable!r}")
        variables.add(variable)
    if "{{" in _PLACEHOLDER_PATTERN.sub("", template) or "}}" in _PLACEHOLDER_PATTERN.sub("", template):
        raise ValueError(f"Prompt template {label} has an unclosed placeholder")
    return variables


def prompt_file_name(value: object) -> str:
    """Return the deterministic, Windows-safe filename stem for an asset."""
    cleaned = _UNSAFE_FILE_NAME.sub("_", str(value or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        raise ValueError("Prompt audit asset_name must not be empty")
    return cleaned


class PromptAuditLogger:
    """Atomically persist exact final prompts and each provider-call history."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = Path(project_dir)

    def write(
        self,
        *,
        asset_type: object,
        asset_name: object,
        prompt: str,
        attempt: int = 1,
    ) -> Path:
        if not isinstance(prompt, str) or not prompt:
            raise ValueError("Final prompt audit requires a non-empty string")
        safe_type = prompt_file_name(asset_type)
        safe_name = prompt_file_name(asset_name)
        root = self.project_dir / "logs" / "prompts" / safe_type
        primary = root / f"{safe_name}.prompt.txt"
        self._atomic_write(primary, prompt)
        history = root / "history" / f"{safe_name}.attempt-{max(1, int(attempt)):02d}.prompt.txt"
        self._atomic_write(history, prompt)
        return primary

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(_normalise_text(text))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink(missing_ok=True)


class PromptStore:
    """Load templates only from ``prompts/<node>/<variant>.md``.

    There deliberately is no root-level-template fallback: a missing node
    directory is a migration error, not a reason to silently use an old prompt.
    """

    def __init__(self, prompt_dir: Path | None = None, *, strict: bool = True) -> None:
        self.prompt_dir = prompt_dir or Path(__file__).resolve().parents[1] / "prompts"
        self.strict = strict

    def template_path(self, node_name: str, *, variant: str = "default") -> Path:
        if not node_name or Path(node_name).name != node_name:
            raise ValueError(f"Invalid prompt node name: {node_name!r}")
        if not variant or Path(variant).name != variant or variant.endswith(".md"):
            raise ValueError(f"Invalid prompt template variant: {variant!r}")
        path = self.prompt_dir / node_name / f"{variant}.md"
        if not path.is_file():
            raise FileNotFoundError(f"Prompt template not found: {path}")
        return path

    def render(self, node_name: str, *, variant: str = "default", **variables: object) -> str:
        path = self.template_path(node_name, variant=variant)
        template = _normalise_text(path.read_text(encoding="utf-8"))
        expected_variables = _template_variables(template, label=f"{node_name}/{variant}")
        supplied_variables = set(variables)
        if self.strict:
            unknown = sorted(supplied_variables.difference(expected_variables))
            if unknown:
                raise ValueError(
                    f"Prompt template {node_name}/{variant} received unknown variables: "
                    f"{', '.join(unknown)}"
                )
            missing = sorted(expected_variables.difference(supplied_variables))
            if missing:
                raise ValueError(
                    f"Prompt template {node_name}/{variant} has unresolved variables: "
                    f"{', '.join(missing)}"
                )
        rendered = _PLACEHOLDER_PATTERN.sub(
            lambda match: str(variables[match.group(1).strip()]),
            template,
        )
        unresolved = sorted(_template_variables(rendered, label=f"{node_name}/{variant}"))
        if unresolved:
            raise ValueError(
                f"Prompt template {node_name}/{variant} has unresolved variables: "
                f"{', '.join(unresolved)}"
            )
        return rendered

    def render_context(
        self,
        node_name: str,
        context: Mapping[str, object],
        *,
        variant: str = "default",
    ) -> str:
        """Render the subset of an explicit content context used by a variant.

        Callers frequently assemble a content context shared by provider-specific
        templates.  This method selects only variables the chosen template uses;
        ``render`` itself remains strict for direct callers.
        """
        template = _normalise_text(self.template_path(node_name, variant=variant).read_text(encoding="utf-8"))
        expected = _template_variables(template, label=f"{node_name}/{variant}")
        missing = sorted(expected.difference(context))
        if missing:
            raise ValueError(
                f"Prompt template {node_name}/{variant} is missing context variables: {', '.join(missing)}"
            )
        return self.render(node_name, variant=variant, **{key: context[key] for key in expected})

    def renderer_path(self, node_name: str) -> Path | None:
        path = self.prompt_dir / node_name / "render.py"
        if path.is_file():
            return path
        if not (self.prompt_dir / node_name).is_dir():
            raise FileNotFoundError(f"Prompt node directory not found: {self.prompt_dir / node_name}")
        return None

    def renderer_fingerprint(self, node_name: str) -> str | None:
        """Hash the deterministic renderer so cached prompts follow renderer edits."""
        path = self.renderer_path(node_name)
        if path is None:
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def render_final(
        self,
        node_name: str,
        model_output: str,
        context: Mapping[str, Any] | None = None,
    ) -> str:
        """Run the optional deterministic renderer for a validated model result."""
        if not isinstance(model_output, str) or not model_output.strip():
            raise ValueError(f"Prompt renderer {node_name} requires non-empty model output")
        path = self.renderer_path(node_name)
        if path is None:
            return model_output
        module = self._load_renderer(path, node_name)
        renderer = getattr(module, "render", None)
        if not callable(renderer):
            raise TypeError(f"Prompt renderer must expose render(model_output, context): {path}")
        result = renderer(model_output, dict(context or {}))
        if not isinstance(result, str) or not result.strip():
            raise ValueError(f"Prompt renderer returned an empty value: {path}")
        return _normalise_text(result)

    @staticmethod
    def _load_renderer(path: Path, node_name: str) -> ModuleType:
        module_name = f"autodrama_prompt_renderer_{node_name}_{abs(hash(path.resolve()))}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load prompt renderer: {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


__all__ = ["PromptAuditLogger", "PromptStore", "prompt_file_name"]
