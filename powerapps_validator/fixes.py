"""Conservative range-based code actions."""
from __future__ import annotations

import re
from typing import Iterable, Sequence

from .diagnostics import (
    Diagnostic,
    FixAction,
    FixApplication,
    FixEdit,
    FixSuggestion,
    SourcePosition,
    diagnostic_id,
    document_revision,
)
from .rules import CONTROLS, DEPRECATED, VARIANT_RULES, validate_text


def _line_at(lines: list[str], line_number: int) -> str:
    return lines[line_number - 1] if 0 < line_number <= len(lines) else ""


def _line_edit(text: str, line: int, replacement: str) -> FixEdit:
    lines = text.splitlines(keepends=True)
    value = lines[line - 1]
    newline = "\n" if value.endswith("\n") else ""
    return FixEdit(SourcePosition(line, 1), SourcePosition(line, len(value.rstrip("\r\n")) + 1), replacement + newline)


def _multiline_edit(start_line: int, end_line: int, replacement: str = "") -> FixEdit:
    return FixEdit(SourcePosition(start_line, 1), SourcePosition(end_line, 10_000), replacement)


def _rename_property_line(line: str, new_name: str) -> str | None:
    match = re.match(r"^(?P<indent> *)(?P<name>[A-Za-z][A-Za-z0-9_]*)(?P<sep>\s*:\s*)(?P<rest>.*)$", line.rstrip("\r\n"))
    if not match:
        return None
    newline = "\n" if line.endswith("\n") else ""
    return f"{match.group('indent')}{new_name}{match.group('sep')}{match.group('rest')}{newline}"


def _variant_rewrite_line(line: str, variant_name: str) -> str | None:
    rules = VARIANT_RULES.get("GroupContainer", {}).get("invalid_map", {})
    mapping = rules.get(variant_name)
    if not mapping:
        return None
    match = re.match(r"^(?P<indent> *)(Variant\s*:\s*)(?P<value>[^\r\n]*)(?P<newline>\r?\n)?$", line.rstrip("\r\n") + ("\n" if line.endswith("\n") else ""))
    if not match:
        return None
    newline = match.group("newline") or ("\n" if line.endswith("\n") else "")
    return f"{match.group('indent')}Variant: {mapping['variant']}{newline}"


def _layout_direction_insert(base_indent: str, direction: str) -> str:
    return f"{base_indent}LayoutDirection: =LayoutDirection.{direction}\n"


def propose_fixes(text: str, diagnostics: Iterable[Diagnostic] | None = None) -> list[FixSuggestion]:
    diagnostics = list(diagnostics) if diagnostics is not None else validate_text(text)
    lines = text.splitlines(keepends=True)
    revision = document_revision(text)
    fixes: list[FixSuggestion] = []
    seen: set[str] = set()

    for diagnostic in diagnostics:
        diag_key = diagnostic_id(diagnostic)
        if diag_key in seen:
            continue
        line_number = diagnostic.range.start.line
        if not 0 < line_number <= len(lines):
            continue
        line = lines[line_number - 1]

        if diagnostic.fix is not None and diagnostic.fix_action in {
            FixAction.DELETE,
            FixAction.RENAME,
            FixAction.REPLACE,
        }:
            if diagnostic.fix_action is FixAction.DELETE:
                title = f"Remove {diagnostic.property_name}"
            elif diagnostic.fix_action is FixAction.RENAME:
                title = f"Rename {diagnostic.property_name}"
            else:
                title = f"Convert {diagnostic.property_name or 'formula'} to YAML block"
            fixes.append(
                FixSuggestion(
                    diagnostic.code,
                    title,
                    diagnostic.message,
                    diagnostic.fix,
                    revision,
                    diagnostic.documentation_url,
                    diagnostic_id=diag_key,
                    why=diagnostic.why,
                )
            )
            seen.add(diag_key)
            continue

        if diagnostic.code == "PAC100":
            match = re.match(
                r"^(?P<prefix>\s*Control\s*:\s*)(?P<name>[A-Za-z][A-Za-z0-9/]+)(?P<suffix>\s*(?:#.*)?)",
                line.rstrip("\r\n"),
            )
            if match:
                name = match.group("name")
                version = CONTROLS.get(name, {}).get("version")
                if version:
                    replacement = f"{match.group('prefix')}{name}@{version}{match.group('suffix')}"
                    fixes.append(
                        FixSuggestion(
                            diagnostic.code,
                            "Pin control version",
                            diagnostic.message,
                            _line_edit(text, line_number, replacement),
                            revision,
                            diagnostic.documentation_url,
                            diagnostic_id=diag_key,
                            why=diagnostic.why,
                        )
                    )
                    seen.add(diag_key)
        elif diagnostic.code == "PAF100":
            match = re.match(r"^(?P<indent> *)(?P<name>[A-Za-z][A-Za-z0-9_]*)(?P<sep>\s*:\s*)(?P<value>[^\r\n]*)", line)
            if match:
                replacement = f"{match.group('indent')}{match.group('name')}{match.group('sep')}={match.group('value').strip()}"
                fixes.append(
                    FixSuggestion(
                        diagnostic.code,
                        "Add Power Fx marker",
                        diagnostic.message,
                        _line_edit(text, line_number, replacement),
                        revision,
                        diagnostic.documentation_url,
                        diagnostic_id=diag_key,
                        why=diagnostic.why,
                    )
                )
                seen.add(diag_key)
        elif diagnostic.code == "PAC102" and diagnostic.fix_action is FixAction.RENAME and diagnostic.fix_rename_to:
            renamed = _rename_property_line(line, diagnostic.fix_rename_to)
            if renamed:
                fixes.append(
                    FixSuggestion(
                        diagnostic.code,
                        f"Rename {diagnostic.property_name}",
                        diagnostic.message,
                        _line_edit(text, line_number, renamed),
                        revision,
                        diagnostic.documentation_url,
                        diagnostic_id=diag_key,
                        why=diagnostic.why,
                    )
                )
                seen.add(diag_key)
        elif diagnostic.code == "PA2108" and diagnostic.fix_action is FixAction.RENAME and diagnostic.fix_rename_to:
            renamed = _rename_property_line(line, diagnostic.fix_rename_to)
            if renamed:
                fixes.append(
                    FixSuggestion(
                        diagnostic.code,
                        f"Rename {diagnostic.property_name}",
                        diagnostic.message,
                        _line_edit(text, line_number, renamed),
                        revision,
                        diagnostic.documentation_url,
                        diagnostic_id=diag_key,
                        why=diagnostic.why,
                    )
                )
                seen.add(diag_key)
        elif diagnostic.code == "PA2109" and diagnostic.fix_action is FixAction.VARIANT_REWRITE:
            variant_match = re.search(r"Variant\s*:\s*(\w+)", line)
            if variant_match:
                rewritten = _variant_rewrite_line(line, variant_match.group(1))
                if rewritten:
                    fixes.append(
                        FixSuggestion(
                            diagnostic.code,
                            "Fix GroupContainer variant",
                            diagnostic.message,
                            _line_edit(text, line_number, rewritten),
                            revision,
                            diagnostic.documentation_url,
                            diagnostic_id=diag_key,
                            why=diagnostic.why,
                        )
                    )
                    seen.add(diag_key)
                    rules = VARIANT_RULES.get("GroupContainer", {}).get("invalid_map", {})
                    mapping = rules.get(variant_match.group(1))
                    if mapping:
                        indent_match = re.match(r"^(\s*)", line)
                        base_indent = indent_match.group(1) if indent_match else "  "
                        insert_line = line_number + 1
                        insert_key = f"variant-layout-{line_number}"
                        if insert_key not in seen:
                            fixes.append(
                                FixSuggestion(
                                    diagnostic.code,
                                    "Add LayoutDirection",
                                    f"Set LayoutDirection to {mapping['layout_direction']}.",
                                    FixEdit(
                                        SourcePosition(insert_line, 1),
                                        SourcePosition(insert_line, 1),
                                        _layout_direction_insert(base_indent, mapping["layout_direction"]),
                                    ),
                                    revision,
                                    diagnostic.documentation_url,
                                    diagnostic_id=insert_key,
                                    why=diagnostic.why,
                                )
                            )
                            seen.add(insert_key)

    return fixes


def _apply_single_edit(lines: list[str], edit: FixEdit) -> bool:
    start = edit.start.line - 1
    end = edit.end.line - 1
    if start < 0 or start >= len(lines):
        return False
    if start == end:
        current = lines[start]
        if edit.replacement == "":
            lines.pop(start)
            return True
        if edit.start.column == 1 and edit.end.column >= len(current.rstrip("\r\n")) + 1:
            lines[start] = edit.replacement
            return True
        stripped = current.rstrip("\r\n")
        prefix = stripped[: edit.start.column - 1] if edit.start.column > 1 else ""
        suffix = stripped[edit.end.column - 1 :] if edit.end.column <= len(stripped) else ""
        newline = "\n" if current.endswith("\n") else ""
        lines[start] = f"{prefix}{edit.replacement}{suffix}{newline}"
        return True
    if end >= len(lines):
        return False
    replacement_lines = edit.replacement.splitlines(keepends=True) if edit.replacement else []
    del lines[start : end + 1]
    for offset, replacement in enumerate(replacement_lines):
        lines.insert(start + offset, replacement)
    return True


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
    for fix in sorted(fixes, key=lambda item: (item.edit.start.line, item.edit.end.line), reverse=True):
        if _apply_single_edit(lines, fix.edit):
            applied.append(fix)
        else:
            skipped.append(fix)
    return FixApplication("".join(lines), tuple(applied), tuple(skipped))
