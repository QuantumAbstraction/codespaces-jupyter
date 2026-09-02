"""Catalog-driven YAML and Power Fx boundary checks."""
from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any, Mapping

from yaml.nodes import MappingNode, ScalarNode

from .diagnostics import Diagnostic, FixAction, FixEdit, Severity, SourcePosition, SourceRange
from .parser import ParsedDocument, items, parse_document, walk

CATALOG = json.loads((Path(__file__).with_name("catalog.json")).read_text(encoding="utf-8"))
ROOT_KEYS = set(CATALOG["roots"])
CONTROLS: Mapping[str, Any] = CATALOG["controls"]
SHARED_PROPERTIES = set(CATALOG.get("shared_properties", []))
DEPRECATED = CATALOG["deprecated_properties"]
VARIANT_RULES = CATALOG.get("variant_rules", {})
DOCS = "https://learn.microsoft.com/en-us/power-apps/maker/canvas-apps/"


def _range(node: Any) -> SourceRange:
    start = SourcePosition(node.start_mark.line + 1, node.start_mark.column + 1)
    end = SourcePosition(node.end_mark.line + 1, node.end_mark.column + 1)
    return SourceRange(start, end)


def _property_value_range(key_node: ScalarNode, value_node: Any) -> SourceRange:
    start = SourcePosition(key_node.start_mark.line + 1, key_node.start_mark.column + 1)
    end = SourcePosition(value_node.end_mark.line + 1, value_node.end_mark.column + 1)
    return SourceRange(start, end)


def _delete_edit(key_node: ScalarNode, value_node: Any) -> FixEdit:
    start_line = key_node.start_mark.line + 1
    end_line = value_node.end_mark.line + 1
    end_col = value_node.end_mark.column + 1
    if end_line == start_line:
        return FixEdit(SourcePosition(start_line, 1), SourcePosition(end_line, end_col + 1), "")
    return FixEdit(SourcePosition(start_line, 1), SourcePosition(end_line, end_col + 1), "")


def _matches_glob(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def _allowed_properties(spec: Mapping[str, Any]) -> set[str]:
    allowed = set(SHARED_PROPERTIES)
    allowed.update(spec.get("properties", []))
    return allowed


def _is_forbidden(property_name: str, spec: Mapping[str, Any]) -> tuple[bool, str | None]:
    forbidden = set(spec.get("forbidden", []))
    if property_name in forbidden:
        return True, spec.get("forbidden_why")
    globs = spec.get("forbidden_globs", [])
    if globs and _matches_glob(property_name, globs):
        return True, spec.get("forbidden_why")
    return False, None


def _diagnostic(
    code: str,
    severity: Severity,
    message: str,
    node: Any,
    path: str,
    *,
    url: str | None = DOCS,
    key_node: ScalarNode | None = None,
    value_node: Any | None = None,
    control_type: str | None = None,
    property_name: str | None = None,
    fix_action: FixAction | None = None,
    fix_rename_to: str | None = None,
    why: str | None = None,
) -> Diagnostic:
    location = _property_value_range(key_node, value_node) if key_node is not None and value_node is not None else _range(node)
    fix = None
    if fix_action is FixAction.DELETE and key_node is not None and value_node is not None:
        fix = _delete_edit(key_node, value_node)
    elif fix_action is FixAction.RENAME and key_node is not None and value_node is not None and fix_rename_to:
        lines_start = key_node.start_mark.line + 1
        fix = FixEdit(SourcePosition(lines_start, 1), SourcePosition(lines_start, key_node.end_mark.column + 1), fix_rename_to)
    return Diagnostic(
        code,
        severity,
        message,
        location,
        path,
        url,
        fix=fix,
        control_type=control_type,
        property_name=property_name,
        fix_action=fix_action,
        fix_rename_to=fix_rename_to,
        why=why,
    )


def _check_variant(base: str, variant_entry: tuple[Any, ScalarNode], path: str, diagnostics: list[Diagnostic]) -> None:
    if base != "GroupContainer":
        return
    variant_value, variant_key = variant_entry
    if not isinstance(variant_value, ScalarNode):
        return
    variant_name = str(variant_value.value).strip()
    rules = VARIANT_RULES.get("GroupContainer", {})
    allowed = set(rules.get("allowed", []))
    if variant_name in allowed:
        return
    invalid_map = rules.get("invalid_map", {})
    if variant_name not in invalid_map:
        spec = CONTROLS.get(base, {})
        allowed_variants = spec.get("variants", [])
        if allowed_variants and variant_name not in allowed_variants:
            diagnostics.append(
                _diagnostic(
                    "PA2109",
                    Severity.ERROR,
                    f"Invalid variant '{variant_name}' for control type 'GroupContainer@1.5.0'.",
                    variant_value,
                    f"{path}.Variant",
                    control_type="GroupContainer@1.5.0",
                    fix_action=FixAction.VARIANT_REWRITE,
                    why=f"GroupContainer supports only {', '.join(allowed_variants)}. Set LayoutDirection for orientation.",
                )
            )
        return
    mapping = invalid_map[variant_name]
    diagnostics.append(
        _diagnostic(
            "PA2109",
            Severity.ERROR,
            f"Invalid variant '{variant_name}' for control type 'GroupContainer@1.5.0'.",
            variant_value,
            f"{path}.Variant",
            control_type="GroupContainer@1.5.0",
            fix_action=FixAction.VARIANT_REWRITE,
            why=f"Use Variant: {mapping['variant']} and LayoutDirection: =LayoutDirection.{mapping['layout_direction']}.",
        )
    )


def validate_document(document: ParsedDocument) -> list[Diagnostic]:
    if document.error:
        error = document.error
        mark = getattr(error, "problem_mark", None)
        node = type("MarkNode", (), {"start_mark": mark, "end_mark": mark})() if mark else None
        location = _range(node) if node else SourceRange(SourcePosition(1, 1), SourcePosition(1, 1))
        return [
            Diagnostic(
                "PAX001",
                Severity.ERROR,
                f"Invalid YAML: {getattr(error, 'problem', str(error))}",
                location,
                "$",
                why="Fix YAML structure, then validate again.",
            )
        ]
    root = document.root
    if root is None or not isinstance(root, MappingNode):
        return [Diagnostic("PAX003", Severity.ERROR, "The document root must be a YAML mapping.")]
    diagnostics: list[Diagnostic] = []
    root_names = set()
    for key, value, key_node in items(root):
        root_names.add(key)
        if key not in ROOT_KEYS:
            diagnostics.append(_diagnostic("PAX004", Severity.ERROR, f"'{key}' is not a supported Power Apps root entity.", key_node, f"$.{key}"))
        if key in {"Screens", "ComponentDefinitions"} and not isinstance(value, MappingNode):
            diagnostics.append(_diagnostic("PAX005", Severity.ERROR, f"{key} must be a mapping.", value, f"$.{key}"))
    if not root_names.intersection(ROOT_KEYS):
        diagnostics.append(_diagnostic("PAX007", Severity.ERROR, "No documented Power Apps root entity was found.", root, "$"))

    for node, path in walk(root):
        entries = list(items(node))
        names = [key for key, _, _ in entries]
        for index, name in enumerate(names):
            if name in names[:index]:
                diagnostics.append(_diagnostic("PAX008", Severity.ERROR, f"Duplicate YAML key '{name}'.", entries[index][2], f"{path}.{name}"))
        values = {key: (value, key_node) for key, value, key_node in entries}
        control = values.get("Control")
        if control:
            control_value, control_key = control
            control_name = str(control_value.value).strip() if isinstance(control_value, ScalarNode) else ""
            base, _, version = control_name.partition("@")
            control_type = f"{base}@{version}" if version else base
            if not isinstance(control_value, ScalarNode) or not base or (version and version.count(".") != 2):
                diagnostics.append(_diagnostic("PAX009", Severity.ERROR, "Control must use ControlName@major.minor.patch format.", control_value, f"{path}.Control"))
                continue
            spec = CONTROLS.get(base)
            if spec is None:
                diagnostics.append(
                    _diagnostic(
                        "PAC101",
                        Severity.WARNING,
                        f"Control type '{control_type}' is not in the validator catalog; property matrix checks skipped.",
                        control_value,
                        f"{path}.Control",
                        control_type=control_type,
                        why="Only catalogued controls receive PA2108 unknown-property checks.",
                    )
                )
            elif spec.get("version") and not version:
                diagnostics.append(_diagnostic("PAC100", Severity.WARNING, f"Pin {base}@{spec['version']} for reproducible source.", control_value, f"{path}.Control"))
            variant_entry = values.get("Variant")
            if variant_entry:
                _check_variant(base, variant_entry, path, diagnostics)
            properties = values.get("Properties")
            if not properties or not isinstance(properties[0], MappingNode):
                continue
            if spec is None or spec.get("skip_property_matrix"):
                continue
            allowed = _allowed_properties(spec)
            renames = {**DEPRECATED, **spec.get("renames", {})}
            for property_name, value, property_key in items(properties[0]):
                prop_path = f"{path}.Properties.{property_name}"
                rename_to = renames.get(property_name)
                if rename_to:
                    code = "PAC102" if property_name in DEPRECATED and base.startswith("Modern") else "PA2108"
                    diagnostics.append(
                        _diagnostic(
                            code,
                            Severity.ERROR,
                            f"Unknown property '{property_name}' for control type '{control_type or base}'; use '{rename_to}'.",
                            property_key,
                            prop_path,
                            key_node=property_key,
                            value_node=value,
                            control_type=control_type,
                            property_name=property_name,
                            fix_action=FixAction.RENAME,
                            fix_rename_to=rename_to,
                            why=f"Rename '{property_name}' to '{rename_to}' for this control type.",
                        )
                    )
                    continue
                forbidden, forbidden_why = _is_forbidden(property_name, spec)
                if forbidden:
                    diagnostics.append(
                        _diagnostic(
                            "PA2108",
                            Severity.ERROR,
                            f"Unknown property '{property_name}' for control type '{control_type}'.",
                            property_key,
                            prop_path,
                            key_node=property_key,
                            value_node=value,
                            control_type=control_type,
                            property_name=property_name,
                            fix_action=FixAction.DELETE,
                            why=forbidden_why or f"'{property_name}' is not valid on {control_type}. Remove the property line.",
                        )
                    )
                    continue
                if property_name not in allowed and spec.get("property_mode", "denylist") == "allowlist":
                    diagnostics.append(
                        _diagnostic(
                            "PA2108",
                            Severity.ERROR,
                            f"Unknown property '{property_name}' for control type '{control_type}'.",
                            property_key,
                            prop_path,
                            key_node=property_key,
                            value_node=value,
                            control_type=control_type,
                            property_name=property_name,
                            fix_action=FixAction.DELETE,
                            why=f"'{property_name}' is not in the catalog allowlist for {control_type}. Remove the property line.",
                        )
                    )
                if base.startswith("Modern") and property_name in DEPRECATED:
                    diagnostics.append(
                        _diagnostic(
                            "PAC102",
                            Severity.ERROR,
                            f"'{property_name}' is deprecated; use '{DEPRECATED[property_name]}'.",
                            property_key,
                            prop_path,
                            key_node=property_key,
                            value_node=value,
                            control_type=control_type,
                            property_name=property_name,
                            fix_action=FixAction.RENAME,
                            fix_rename_to=DEPRECATED[property_name],
                        )
                    )
                if isinstance(value, ScalarNode) and value.tag != "tag:yaml.org,2002:null":
                    formula = str(value.value).strip()
                    if not formula.startswith("="):
                        diagnostics.append(_diagnostic("PAF100", Severity.ERROR, "Property values must be Power Fx formulas beginning with '='.", value, prop_path))
                    if "\n" in str(value.value) and value.style != "|":
                        diagnostics.append(_diagnostic("PAF101", Severity.WARNING, "Multiline Power Fx should use the YAML literal block style '|-'.", value, prop_path))
                if property_name in {"Text", "Tooltip", "AccessibleLabel"} and isinstance(value, ScalarNode) and '"' in str(value.value) and "nfBi(" not in str(value.value):
                    diagnostics.append(_diagnostic("PAO100", Severity.INFO, "Bilingual user-facing text should use nfBi(EN, FR).", value, prop_path))
        elif "Properties" in values and not isinstance(values["Properties"][0], MappingNode):
            diagnostics.append(_diagnostic("PAX010", Severity.ERROR, "Properties must be a mapping.", values["Properties"][0], f"{path}.Properties"))
    return sorted(diagnostics, key=lambda item: (item.range.start.line, item.range.start.column, item.code))


def validate_text(text: str, source_name: str = "<memory>", schema: Mapping[str, Any] | None = None) -> list[Diagnostic]:
    del source_name, schema
    return validate_document(parse_document(text))
