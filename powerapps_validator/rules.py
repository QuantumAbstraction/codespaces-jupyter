"""Catalog-driven YAML and Power Fx boundary checks."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping
import yaml
from yaml.nodes import MappingNode, ScalarNode
from .diagnostics import Diagnostic, Severity, SourcePosition, SourceRange
from .parser import ParsedDocument, items, parse_document, walk

CATALOG = json.loads((Path(__file__).with_name("catalog.json")).read_text(encoding="utf-8"))
ROOT_KEYS = set(CATALOG["roots"])
CONTROLS: Mapping[str, Any] = CATALOG["controls"]
DEPRECATED = CATALOG["deprecated_properties"]
DOCS = "https://learn.microsoft.com/power-apps/maker/canvas-apps/"


def _range(node: Any) -> SourceRange:
    start = SourcePosition(node.start_mark.line + 1, node.start_mark.column + 1)
    end = SourcePosition(node.end_mark.line + 1, node.end_mark.column + 1)
    return SourceRange(start, end)


def _diagnostic(code: str, severity: Severity, message: str, node: Any, path: str, *, url: str | None = DOCS) -> Diagnostic:
    return Diagnostic(code, severity, message, _range(node), path, url)


def validate_document(document: ParsedDocument) -> list[Diagnostic]:
    if document.error:
        error = document.error
        mark = getattr(error, "problem_mark", None)
        node = type("MarkNode", (), {"start_mark": mark, "end_mark": mark})() if mark else None
        location = _range(node) if node else SourceRange(SourcePosition(1, 1), SourcePosition(1, 1))
        return [Diagnostic("PAX001", Severity.ERROR, f"Invalid YAML: {getattr(error, 'problem', str(error))}", location, "$")]
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
        diagnostics.append(_diagnostic("PAX007", Severity.ERROR, "No documented Power Apps root entity was found.", root, "$") )

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
            if not isinstance(control_value, ScalarNode) or not base or (version and version.count(".") != 2):
                diagnostics.append(_diagnostic("PAX009", Severity.ERROR, "Control must use ControlName@major.minor.patch format.", control_value, f"{path}.Control"))
                continue
            spec = CONTROLS.get(base)
            if spec and not version:
                diagnostics.append(_diagnostic("PAC100", Severity.WARNING, f"Pin {base}@{spec['version']} for reproducible source.", control_value, f"{path}.Control"))
            properties = values.get("Properties")
            if not properties or not isinstance(properties[0], MappingNode):
                continue
            for property_name, value, property_key in items(properties[0]):
                if base.startswith("Modern") and property_name in DEPRECATED:
                    diagnostics.append(_diagnostic("PAC102", Severity.ERROR, f"'{property_name}' is deprecated; use '{DEPRECATED[property_name]}'.", property_key, f"{path}.Properties.{property_name}"))
                if isinstance(value, ScalarNode) and value.tag != "tag:yaml.org,2002:null":
                    formula = str(value.value).strip()
                    if not formula.startswith("="):
                        diagnostics.append(_diagnostic("PAF100", Severity.ERROR, "Property values must be Power Fx formulas beginning with '='.", value, f"{path}.Properties.{property_name}"))
                    if "\n" in str(value.value) and value.style != "|":
                        diagnostics.append(_diagnostic("PAF101", Severity.WARNING, "Multiline Power Fx should use the YAML literal block style '|-'.", value, f"{path}.Properties.{property_name}"))
                if property_name in {"Text", "Tooltip", "AccessibleLabel"} and isinstance(value, ScalarNode) and '"' in str(value.value) and "nfBi(" not in str(value.value):
                    diagnostics.append(_diagnostic("PAO100", Severity.INFO, "Bilingual user-facing text should use nfBi(EN, FR).", value, f"{path}.Properties.{property_name}"))
        elif "Properties" in values and not isinstance(values["Properties"][0], MappingNode):
            diagnostics.append(_diagnostic("PAX010", Severity.ERROR, "Properties must be a mapping.", values["Properties"][0], f"{path}.Properties"))
    return sorted(diagnostics, key=lambda item: (item.range.start.line, item.range.start.column, item.code))


def validate_text(text: str, source_name: str = "<memory>", schema: Mapping[str, Any] | None = None) -> list[Diagnostic]:
    del source_name, schema
    return validate_document(parse_document(text))
