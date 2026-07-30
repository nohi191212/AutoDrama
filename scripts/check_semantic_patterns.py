from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = (
    REPOSITORY_ROOT / "autodrama" / "src",
    REPOSITORY_ROOT / "scripts",
)
SELF_PATHS = {
    Path(__file__).resolve(),
    (REPOSITORY_ROOT / "scripts" / "smoke" / "semantic_schema_contract_smoke.py").resolve(),
}
SUSPICIOUS_NAME = re.compile(r"(?:PATTERNS?|KEYWORDS?|MARKERS?|REWRITES?|PHRASES?)$", re.IGNORECASE)
CJK_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
WORD_SEQUENCE = re.compile(r"[A-Za-z]{2,}(?:\s+[A-Za-z]{2,})+")
URL_OR_PROTOCOL = re.compile(r"^(?:https?://|data:|[a-z][a-z0-9+.-]*://)", re.IGNORECASE)
REGEX_CALLS = {"compile", "search", "match", "fullmatch", "findall", "finditer", "sub", "subn"}
DATA_FILE_ENDINGS = (".json", ".yaml", ".yml", ".json.example", ".yaml.example", ".yml.example")
DATA_SKIP_PARTS = {".git", ".tmp", "__pycache__"}


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    line: int
    column: int
    end_line: int
    end_column: int
    category: str
    scope: str
    summary: str
    code_kind: str

    @property
    def identity(self) -> tuple[object, ...]:
        return (
            self.path,
            self.line,
            self.column,
            self.end_line,
            self.end_column,
            self.category,
        )


def _literal_strings(node: ast.AST) -> list[str]:
    return [
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    ]


def _looks_natural_language(value: str) -> bool:
    text = value.strip()
    if not text or URL_OR_PROTOCOL.match(text):
        return False
    if CJK_CHARACTER.search(text):
        return True
    if WORD_SEQUENCE.search(text):
        return True
    return False


def _contains_natural_language(node: ast.AST, minimum: int = 1) -> bool:
    return sum(_looks_natural_language(value) for value in _literal_strings(node)) >= minimum


def _looks_semantic_collection(node: ast.AST) -> bool:
    values = _literal_strings(node)
    if any(_looks_natural_language(value) for value in values):
        return True
    simple_words = [value for value in values if value.isalpha() and len(value) >= 2]
    return len(simple_words) >= 2


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _scope_name(parents: Sequence[ast.AST]) -> str:
    for parent in reversed(parents):
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return parent.name
    return "<module>"


def _mechanical_message_scope(scope: str) -> bool:
    lowered = scope.lower()
    return any(fragment in lowered for fragment in ("error", "failure", "retry", "already_exists"))


class SemanticPatternVisitor(ast.NodeVisitor):
    def __init__(self, relative_path: str, source: str) -> None:
        self.relative_path = relative_path
        self.source_lines = source.splitlines()
        self.parents: list[ast.AST] = []
        self.findings: list[Finding] = []
        self.suspicious_bindings: set[str] = set()
        self.containment_by_scope: dict[str, list[ast.Compare]] = {}
        self.replacements_by_scope: dict[str, list[ast.Call]] = {}

    def visit(self, node: ast.AST) -> None:
        self.parents.append(node)
        super().visit(node)
        self.parents.pop()

    def _add(self, node: ast.AST, category: str, summary: str) -> None:
        line = getattr(node, "lineno", 1)
        snippet = self.source_lines[line - 1].strip() if line <= len(self.source_lines) else ""
        if len(snippet) > 120:
            snippet = snippet[:117] + "..."
        self.findings.append(
            Finding(
                path=self.relative_path,
                line=line,
                column=getattr(node, "col_offset", 0),
                end_line=getattr(node, "end_lineno", line),
                end_column=getattr(node, "end_col_offset", 0),
                category=category,
                scope=_scope_name(self.parents[:-1]),
                summary=f"{summary}: {snippet}",
                code_kind="script" if self.relative_path.startswith("scripts/") else "production",
            )
        )

    def visit_Assign(self, node: ast.Assign) -> None:
        names = [target.id for target in node.targets if isinstance(target, ast.Name)]
        if (
            names
            and any(SUSPICIOUS_NAME.search(name) for name in names)
            and not any(name.upper().startswith("SECRET_") for name in names)
            and not _mechanical_message_scope(_scope_name(self.parents[:-1]))
            and isinstance(node.value, (ast.List, ast.Tuple, ast.Set, ast.Dict))
            and _looks_semantic_collection(node.value)
        ):
            self.suspicious_bindings.update(names)
            self._add(node, "named-semantic-collection", "semantic collection name")
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if (
            isinstance(node.target, ast.Name)
            and SUSPICIOUS_NAME.search(node.target.id)
            and not node.target.id.upper().startswith("SECRET_")
            and not _mechanical_message_scope(_scope_name(self.parents[:-1]))
            and node.value is not None
            and isinstance(node.value, (ast.List, ast.Tuple, ast.Set, ast.Dict))
            and _looks_semantic_collection(node.value)
        ):
            self.suspicious_bindings.add(node.target.id)
            self._add(node, "named-semantic-collection", "semantic collection name")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        call_name = _call_name(node)
        if call_name in {"any", "all", "sum"} and node.args:
            expression = node.args[0]
            has_containment = any(
                isinstance(child, ast.Compare)
                and any(isinstance(operator, (ast.In, ast.NotIn)) for operator in child.ops)
                for child in ast.walk(expression)
            )
            iterates_suspicious_name = any(
                isinstance(child, ast.Name) and child.id in self.suspicious_bindings
                for child in ast.walk(expression)
            )
            scope = _scope_name(self.parents[:-1])
            if (
                has_containment
                and not _mechanical_message_scope(scope)
                and (_contains_natural_language(expression) or iterates_suspicious_name)
            ):
                self._add(node, "semantic-membership-aggregate", f"{call_name}() membership aggregate")

        if call_name in REGEX_CALLS and node.args:
            pattern_node = node.args[0]
            if isinstance(pattern_node, ast.Constant) and isinstance(pattern_node.value, str):
                alternatives = pattern_node.value.split("|")
                if len(alternatives) >= 2 and sum(_looks_natural_language(item) for item in alternatives) >= 2:
                    self._add(node, "natural-language-regex", "natural-language regex alternation")

        if isinstance(node.func, ast.Attribute) and node.func.attr == "replace" and node.args:
            if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                if _looks_natural_language(node.args[0].value):
                    scope = _scope_name(self.parents[:-1])
                    self.replacements_by_scope.setdefault(scope, []).append(node)
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        if any(isinstance(operator, (ast.In, ast.NotIn)) for operator in node.ops):
            scope = _scope_name(self.parents[:-1])
            inside_assert = any(isinstance(parent, ast.Assert) for parent in self.parents[:-1])
            if _contains_natural_language(node) and not inside_assert and not _mechanical_message_scope(scope):
                scope = _scope_name(self.parents[:-1])
                self.containment_by_scope.setdefault(scope, []).append(node)
            literal_collection = any(
                isinstance(part, (ast.List, ast.Tuple, ast.Set)) and _contains_natural_language(part)
                for part in (node.left, *node.comparators)
            )
            if literal_collection and not inside_assert:
                self._add(node, "literal-semantic-membership", "literal collection membership")
        self.generic_visit(node)

    def finish(self) -> list[Finding]:
        for scope, comparisons in self.containment_by_scope.items():
            if len(comparisons) >= 2:
                for comparison in comparisons:
                    self._add(comparison, "repeated-natural-language-membership", f"repeated membership in {scope}")
        for scope, calls in self.replacements_by_scope.items():
            if len(calls) >= 2:
                for call in calls:
                    self._add(call, "natural-language-rewrite", f"multiple replacements in {scope}")
        return sorted(set(self.findings))


def inspect_source(source: str, relative_path: str = "fixture.py") -> list[Finding]:
    tree = ast.parse(source, filename=relative_path)
    visitor = SemanticPatternVisitor(relative_path.replace("\\", "/"), source)
    visitor.visit(tree)
    return visitor.finish()


def discover_python_files(scan_roots: Iterable[Path] = SCAN_ROOTS) -> list[Path]:
    files: set[Path] = set()
    for scan_root in scan_roots:
        if scan_root.exists():
            files.update(path.resolve() for path in scan_root.rglob("*.py"))
    return sorted(path for path in files if path not in SELF_PATHS and "__pycache__" not in path.parts)


def inspect_repository(scan_roots: Iterable[Path] = SCAN_ROOTS) -> list[Finding]:
    findings: list[Finding] = []
    for path in discover_python_files(scan_roots):
        relative_path = path.relative_to(REPOSITORY_ROOT).as_posix()
        try:
            source = path.read_text(encoding="utf-8-sig")
            findings.extend(inspect_source(source, relative_path))
        except (SyntaxError, UnicodeDecodeError) as error:
            print(f"error: cannot inspect {relative_path}: {error}", file=sys.stderr)
            raise
    findings.extend(inspect_data_files())
    return sorted(findings)


def _data_semantic_paths(value: object, path: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    findings: list[tuple[str, ...]] = []
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key)
            child_path = (*path, key)
            semantic_values = _data_rule_strings(child)
            if (
                SUSPICIOUS_NAME.search(key)
                and isinstance(child, (list, tuple, set, dict))
                and (
                    any(_looks_natural_language(item) for item in semantic_values)
                    or len([item for item in semantic_values if item.isalpha() and len(item) >= 2]) >= 2
                )
            ):
                findings.append(child_path)
            findings.extend(_data_semantic_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(_data_semantic_paths(child, (*path, str(index))))
    return findings


def _data_rule_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for child in value for item in _data_rule_strings(child)]
    if isinstance(value, dict):
        natural_keys = [str(key) for key in value if _looks_natural_language(str(key))]
        return natural_keys + [item for child in value.values() for item in _data_rule_strings(child)]
    return []


def discover_data_files() -> list[Path]:
    files: set[Path] = set()
    for scan_root in SCAN_ROOTS:
        if scan_root.exists():
            files.update(
                path.resolve()
                for path in scan_root.rglob("*")
                if path.name.lower().endswith(DATA_FILE_ENDINGS)
            )
    files.update(
        path.resolve()
        for path in REPOSITORY_ROOT.iterdir()
        if path.is_file() and path.name.lower().endswith(DATA_FILE_ENDINGS)
    )
    return sorted(
        path
        for path in files
        if not any(part in DATA_SKIP_PARTS for part in path.relative_to(REPOSITORY_ROOT).parts)
    )


def inspect_data_files() -> list[Finding]:
    findings: list[Finding] = []
    for path in discover_data_files():
        relative_path = path.relative_to(REPOSITORY_ROOT).as_posix()
        source = path.read_text(encoding="utf-8-sig")
        try:
            is_json = path.name.lower().endswith((".json", ".json.example"))
            payload = json.loads(source) if is_json else yaml.safe_load(source)
        except (json.JSONDecodeError, yaml.YAMLError) as error:
            print(f"error: cannot inspect {relative_path}: {error}", file=sys.stderr)
            raise
        for data_path in _data_semantic_paths(payload):
            key = data_path[-1]
            line = next(
                (
                    index
                    for index, text in enumerate(source.splitlines(), start=1)
                    if re.search(rf"^\s*[\"']?{re.escape(key)}[\"']?\s*:", text)
                ),
                1,
            )
            findings.append(
                Finding(
                    path=relative_path,
                    line=line,
                    column=0,
                    end_line=line,
                    end_column=0,
                    category="data-semantic-collection",
                    scope=".".join(data_path[:-1]) or "<root>",
                    summary=f"natural-language rule collection stored under {'.'.join(data_path)}",
                    code_kind="data",
                )
            )
    return findings


def _print_finding(prefix: str, finding: Finding) -> None:
    print(
        f"{prefix} {finding.path}:{finding.line}:{finding.column + 1} "
        f"[{finding.code_kind}/{finding.category}] {finding.summary}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit natural-language semantic patterns in Python code.")
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args(argv)

    findings = inspect_repository()
    for finding in findings:
        _print_finding("FOUND", finding)
    if args.report_only:
        print(f"Semantic pattern audit: {len(findings)} finding(s), report-only mode.")
        return 0
    if findings:
        print(f"Semantic pattern audit failed: {len(findings)} finding(s).", file=sys.stderr)
        return 1
    print("Semantic pattern audit passed: zero findings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
