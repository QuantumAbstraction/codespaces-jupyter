"""Conservative range-based code actions."""
from __future__ import annotations
import re
from typing import Iterable, Sequence
from .diagnostics import Diagnostic, FixApplication, FixEdit, FixSuggestion, SourcePosition, document_revision
from .rules import CONTROLS, DEPRECATED, validate_text


def _offsets(text: str) -> list[int]:
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _line_edit(text: str, line: int, replacement: str) -> FixEdit:
    lines = text.splitlines(keepends=True)
    value = lines[line - 1]
    newline = "\n" if value.endswith("\n") else ""
    return FixEdit(SourcePosition(line, 1), SourcePosition(line, len(value.rstrip("\r\n")) + 1), replacement + newline)


def propose_fixes(text: str, diagnostics: Iterable[Diagnostic] | None = None) -> list[FixSuggestion]:
    diagnostics = list(diagnostics) if diagnostics is not None else validate_text(text)
    lines = text.splitlines(keepends=True)
    revision = document_revision(text)
    fixes: list[FixSuggestion] = []
    for diagnostic in diagnostics:
        line_number = diagnostic.range.start.line
        if not 0 < line_number <= len(lines):
            continue
        line = lines[line_number - 1]
        if diagnostic.code == "PAC100":
            match = re.match(r"^(?P<prefix>\s*Control\s*:\s*)(?P<name>Modern[A-Za-z0-9]+)(?P<suffix>\s*(?:#.*)?)", line.rstrip("\r\n"))
            if match and match.group("name") in CONTROLS:
                replacement = f"{match.group('prefix')}{match.group('name')}@{CONTROLS[match.group('name')]['version']}{match.group('suffix')}"
                fixes.append(FixSuggestion(diagnostic.code, "Pin control version", diagnostic.message, _line_edit(text, line_number, replacement), revision, diagnostic.documentation_url))
        elif diagnostic.code == "PAF100":
            match = re.match(r"^(?P<indent> *)(?P<name>[A-Za-z][A-Za-z0-9_]*)(?P<sep>\s*:\s*)(?P<value>[^\r\n]*)", line)
            if match:
                replacement = f"{match.group('indent')}{match.group('name')}{match.group('sep')}={match.group('value').strip()}"
                fixes.append(FixSuggestion(diagnostic.code, "Add Power Fx marker", diagnostic.message, _line_edit(text, line_number, replacement), revision, diagnostic.documentation_url))
        elif diagnostic.code == "PAC102":
            match = re.match(r"^(?P<indent> *)(?P<name>[A-Za-z][A-Za-z0-9_]*)(?P<sep>\s*:\s*)(?P<value>.*?)(?:\r?\n)?$", line)
            replacement = DEPRECATED.get(match.group("name")) if match else None
            if replacement:
                fixes.append(FixSuggestion(diagnostic.code, f"Rename {match.group('name')}", diagnostic.message, _line_edit(text, line_number, f"{match.group('indent')}{replacement}{match.group('sep')}{match.group('value')}"), revision, diagnostic.documentation_url))
    return fixes


def apply_fixes(text: str, fixes: Sequence[FixSuggestion], *, expected_revision: str | None = None) -> FixApplication:
    revision = document_revision(text)
    if expected_revision is not None and revision != expected_revision:
        return FixApplication(text, (), tuple(fixes), True)
    stale = [fix for fix in fixes if fix.revision != revision]
    if stale:
        return FixApplication(text, (), tuple(fixes), True)
    lines = text.splitlines(keepends=True)
    applied: list[FixSuggestion] = []
    skipped: list[FixSuggestion] = []
    for fix in sorted(fixes, key=lambda item: item.edit.start.line, reverse=True):
        index = fix.edit.start.line - 1
        expected = lines[index] if 0 <= index < len(lines) else None
        current = expected.rstrip("\r\n") if expected is not None else None
        if current is None or fix.edit.start.column != 1 or fix.edit.end.line != fix.edit.start.line:
            skipped.append(fix)
            continue
        lines[index] = fix.edit.replacement
        applied.append(fix)
    return FixApplication("".join(lines), tuple(applied), tuple(skipped))
