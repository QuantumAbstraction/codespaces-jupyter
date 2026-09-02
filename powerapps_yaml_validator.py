"""Notebook-friendly validation and repair helpers for Power Apps YAML."""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence

import ipywidgets as widgets
import yaml
from IPython.display import Javascript, display
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Diagnostic:
    code: str
    severity: Severity
    message: str
    line: int = 1
    column: int = 1
    path: str = "$"

    def format(self, source_name: str = "<memory>") -> str:
        return f"{source_name}:{self.line}:{self.column}: {self.severity.value} {self.code}: {self.message} [{self.path}]"


@dataclass(frozen=True)
class FixSuggestion:
    code: str
    title: str
    message: str
    line: int
    before: str
    after: str


@dataclass(frozen=True)
class FixApplication:
    text: str
    applied: tuple[FixSuggestion, ...]
    skipped: tuple[FixSuggestion, ...]


ROOT_KEYS = {"App", "Screens", "ComponentDefinitions", "DataSources", "EditorState"}
CONTROL_RE = re.compile(r"^(?P<name>[A-Za-z][A-Za-z0-9]*(?:/[A-Za-z][A-Za-z0-9]*)?)(?:@(?P<version>\d+\.\d+\.\d+))?$")
PROPERTY_RE = re.compile(r"^(?P<indent> *)(?P<name>[A-Za-z][A-Za-z0-9_]*)(?P<sep>\s*:\s*)(?P<value>[^\r\n]*)(?P<newline>\r?\n)?$")
CONTROL_LINE_RE = re.compile(r"^(?P<prefix>\s*Control\s*:\s*)(?P<name>Modern[A-Za-z0-9]+)(?P<suffix>\s*(?:#.*)?)(?P<newline>\r?\n)?$")
MODERN_VERSIONS = {"ModernButton": "1.0.0", "ModernTextInput": "1.0.0", "ModernDropdown": "1.0.0", "ModernSlider": "1.0.0", "ModernIcon": "1.1.0"}
DEPRECATED = {"FontColor": "Color", "FontSize": "Size", "FontItalic": "Italic", "FontUnderline": "Underline"}


def _diagnostic(code: str, severity: Severity, message: str, node: Node, path: str = "$") -> Diagnostic:
    return Diagnostic(code, severity, message, node.start_mark.line + 1, node.start_mark.column + 1, path)


def _items(node: MappingNode) -> Iterable[tuple[str, Node, ScalarNode]]:
    for key, value in node.value:
        if isinstance(key, ScalarNode):
            yield str(key.value), value, key


def _walk(node: Node, path: str = "$") -> Iterable[tuple[MappingNode, str]]:
    if isinstance(node, MappingNode):
        yield node, path
        for key, value, _ in _items(node):
            yield from _walk(value, f"{path}.{key}")
    elif isinstance(node, SequenceNode):
        for index, value in enumerate(node.value):
            yield from _walk(value, f"{path}[{index}]")


def validate_text(text: str, source_name: str = "<memory>", schema: Mapping[str, Any] | None = None) -> list[Diagnostic]:
    """Validate common source-shape, control-version, and formula mistakes."""
    del source_name, schema
    try:
        document = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        return [Diagnostic("PAX001", Severity.ERROR, f"Invalid YAML: {getattr(error, 'problem', str(error))}", mark.line + 1 if mark else 1, mark.column + 1 if mark else 1)]
    if document is None or not isinstance(document, MappingNode):
        return [Diagnostic("PAX003", Severity.ERROR, "The document root must be a YAML mapping.")]

    diagnostics: list[Diagnostic] = []
    root_names = set()
    for key, value, key_node in _items(document):
        root_names.add(key)
        if key not in ROOT_KEYS:
            diagnostics.append(_diagnostic("PAX004", Severity.ERROR, f"'{key}' is not a supported Power Apps root entity.", key_node, f"$.{key}"))
        if key in {"Screens", "ComponentDefinitions"} and not isinstance(value, MappingNode):
            diagnostics.append(_diagnostic("PAX005", Severity.ERROR, f"{key} must be a mapping.", value, f"$.{key}"))
    if not root_names.intersection(ROOT_KEYS):
        diagnostics.append(_diagnostic("PAX007", Severity.ERROR, "No documented Power Apps root entity was found.", document))

    for node, path in _walk(document):
        entries = list(_items(node))
        names = [key for key, _, _ in entries]
        for index, name in enumerate(names):
            if name in names[:index]:
                diagnostics.append(_diagnostic("PAX008", Severity.ERROR, f"Duplicate YAML key '{name}'.", entries[index][2], f"{path}.{name}"))
        values = {key: (value, key_node) for key, value, key_node in entries}
        control = values.get("Control")
        if control:
            control_value, _ = control
            if not isinstance(control_value, ScalarNode) or not CONTROL_RE.fullmatch(str(control_value.value).strip()):
                diagnostics.append(_diagnostic("PAX009", Severity.ERROR, "Control must use ControlName@major.minor.patch format.", control_value, f"{path}.Control"))
                continue
            match = CONTROL_RE.fullmatch(str(control_value.value).strip())
            expected = MODERN_VERSIONS.get(match.group("name"))
            if expected and match.group("version") is None:
                diagnostics.append(_diagnostic("PAC100", Severity.WARNING, f"Pin {match.group('name')}@{expected} for reproducible source.", control_value, f"{path}.Control"))
            properties = values.get("Properties")
            if not properties or not isinstance(properties[0], MappingNode):
                continue
            for property_name, value, property_key in _items(properties[0]):
                if match.group("name").startswith("Modern") and property_name in DEPRECATED:
                    diagnostics.append(_diagnostic("PAC102", Severity.ERROR, f"'{property_name}' is deprecated; use '{DEPRECATED[property_name]}'.", property_key, f"{path}.Properties.{property_name}"))
                if isinstance(value, ScalarNode) and value.tag != "tag:yaml.org,2002:null" and not str(value.value).strip().startswith("="):
                    diagnostics.append(_diagnostic("PAF100", Severity.ERROR, "Property values must be Power Fx formulas beginning with '='.", value, f"{path}.Properties.{property_name}"))
        elif "Properties" in values and not isinstance(values["Properties"][0], MappingNode):
            diagnostics.append(_diagnostic("PAX010", Severity.ERROR, "Properties must be a mapping.", values["Properties"][0], f"{path}.Properties"))
    return sorted(diagnostics, key=lambda item: (item.line, item.column, item.code))


def propose_fixes(text: str, diagnostics: Iterable[Diagnostic] | None = None) -> list[FixSuggestion]:
    diagnostics = list(diagnostics) if diagnostics is not None else validate_text(text)
    lines = text.splitlines(keepends=True)
    fixes: list[FixSuggestion] = []
    for diagnostic in diagnostics:
        if not 0 < diagnostic.line <= len(lines):
            continue
        line = lines[diagnostic.line - 1]
        if diagnostic.code == "PAC100":
            match = CONTROL_LINE_RE.fullmatch(line)
            if match and match.group("name") in MODERN_VERSIONS:
                after = f"{match.group('prefix')}{match.group('name')}@{MODERN_VERSIONS[match.group('name')]}{match.group('suffix')}{match.group('newline') or ''}"
                fixes.append(FixSuggestion(diagnostic.code, "Pin control version", diagnostic.message, diagnostic.line, line, after))
        elif diagnostic.code == "PAF100":
            match = PROPERTY_RE.fullmatch(line)
            if match:
                after = f"{match.group('indent')}{match.group('name')}{match.group('sep')}={match.group('value').strip()}{match.group('newline') or ''}"
                fixes.append(FixSuggestion(diagnostic.code, "Add Power Fx marker", diagnostic.message, diagnostic.line, line, after))
        elif diagnostic.code == "PAC102":
            match = PROPERTY_RE.fullmatch(line)
            replacement = DEPRECATED.get(match.group("name")) if match else None
            if replacement:
                after = f"{match.group('indent')}{replacement}{match.group('sep')}{match.group('value')}{match.group('newline') or ''}"
                fixes.append(FixSuggestion(diagnostic.code, f"Rename {match.group('name')}", diagnostic.message, diagnostic.line, line, after))
    return fixes


def apply_fixes(text: str, fixes: Sequence[FixSuggestion]) -> FixApplication:
    lines = text.splitlines(keepends=True)
    applied, skipped = [], []
    for fix in sorted(fixes, key=lambda item: item.line, reverse=True):
        index = fix.line - 1
        if index < 0 or index >= len(lines) or lines[index] != fix.before:
            skipped.append(fix)
        else:
            lines[index] = fix.after
            applied.append(fix)
    return FixApplication("".join(lines), tuple(applied), tuple(skipped))


# The notebook keeps this import path for compatibility; validation and edits
# are implemented by the host-neutral package above the widget layer.
from powerapps_validator import (
    Diagnostic as Diagnostic,
    FixApplication as FixApplication,
    FixEdit as FixEdit,
    FixSuggestion as FixSuggestion,
    Severity as Severity,
    apply_fixes as apply_fixes,
    document_revision as document_revision,
    propose_fixes as propose_fixes,
    validate_text as validate_text,
)


class PowerAppsYamlValidatorUI:
    """Reliable native-widget UI with no CDN, iframe, or custom editor bridge."""

    def __init__(self, initial_yaml: str = "", *, source_name: str = "canvas.pa.yaml", on_apply: Callable[[str], None] | None = None) -> None:
        self.source_name, self.on_apply = source_name, on_apply
        area = {"width": "100%", "height": "190px"}
        self.source = widgets.Textarea(value=initial_yaml, description="Source", layout=widgets.Layout(**area), style={"description_width": "initial"})
        self.preview = widgets.Textarea(description="Preview", disabled=True, layout=widgets.Layout(**area), style={"description_width": "initial"})
        self.line_numbers = widgets.HTML()
        self.validate_button = widgets.Button(description="Validate", icon="check", button_style="primary")
        self.preview_button = widgets.Button(description="Preview fixes", icon="eye", disabled=True)
        self.apply_button = widgets.Button(description="Apply fixes", icon="check-circle", disabled=True)
        self.copy_button = widgets.Button(description="Copy preview", icon="copy", disabled=True)
        self.status, self.diagnostics_view = widgets.HTML(), widgets.HTML()
        self._diagnostics: list[Diagnostic] = []
        self._fixes: list[FixSuggestion] = []
        self._source_snapshot: str | None = None
        self.validate_button.on_click(lambda _: self.validate())
        self.preview_button.on_click(lambda _: self.preview_selected())
        self.apply_button.on_click(lambda _: self.apply_selected())
        self.copy_button.on_click(lambda _: self.copy_preview())
        self.source.observe(self._source_changed, names="value")
        self._update_line_numbers()
        self._theme = widgets.HTML("""
<style>
.ayu-validator,
.ayu-validator .widget-container,
.ayu-validator .widget-box,
.ayu-validator .widget-vbox,
.ayu-validator .widget-hbox,
.ayu-validator .jupyter-widgets { background: #180b0d !important; color: #f2d9d9 !important; }
.ayu-validator { width: min(100% - 32px, 1180px) !important; max-width: 1180px !important; max-height: min(720px, calc(100vh - 32px)) !important; margin: 20px auto !important; padding: 28px 32px !important; border: 2px solid #a8323d !important; border-radius: 12px !important; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; box-sizing: border-box; overflow-y: auto !important; overflow-x: hidden !important; }
.ayu-validator .ayu-header { padding: 4px 0 18px; border-bottom: 1px solid #5a2228; margin-bottom: 18px; }
.ayu-validator .ayu-kicker { color: #ff6b6b; font: 700 11px/1.2 ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing: 1.4px; text-transform: uppercase; }
.ayu-validator h2 { color: #ffe1e1; font: 600 25px/1.2 Georgia, serif; margin: 7px 0 5px; }
.ayu-validator .ayu-subtitle { color: #b98f93; font-size: 13px; }
.ayu-validator .ayu-label { color: #e5bfc1; font: 600 12px/1.2 ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing: .6px; margin: 14px 0 7px; }
.ayu-validator .widget-textarea,
.ayu-validator .widget-textarea .widget-input { border: 2px solid #a8323d !important; border-radius: 6px !important; overflow: hidden; background: #100709 !important; }
.ayu-validator .widget-textarea textarea { background: #100709 !important; color: #ffe1e1 !important; border: 0 !important; font: 13px/1.55 ui-monospace, SFMono-Regular, Menlo, monospace !important; padding: 13px !important; resize: vertical; }
.ayu-validator .widget-textarea textarea:focus { outline: 1px solid #ff6b6b !important; }
.ayu-validator .ayu-actions { width: 100% !important; margin: 18px 0 8px; gap: 10px !important; flex-wrap: wrap !important; }
.ayu-validator .ayu-actions { position: sticky !important; top: 0 !important; z-index: 10 !important; padding: 10px 0 !important; margin-top: 0 !important; background: #180b0d !important; border-bottom: 1px solid #5a2228 !important; }
.ayu-validator .ayu-actions .widget-button { display: inline-flex !important; visibility: visible !important; opacity: 1 !important; min-height: 36px !important; }
.ayu-validator .ayu-actions .widget-button button { display: inline-flex !important; visibility: visible !important; opacity: 1 !important; min-height: 36px !important; align-items: center !important; justify-content: center !important; }
.ayu-validator .ayu-source-row { width: 100% !important; align-items: stretch !important; gap: 0 !important; }
.ayu-validator .ayu-line-numbers { flex: 0 0 42px !important; width: 42px !important; min-width: 42px !important; padding: 13px 8px 13px 4px !important; border: 2px solid #a8323d !important; border-right: 0 !important; border-radius: 6px 0 0 6px !important; background: #240b10 !important; color: #b98f93 !important; font: 13px/1.55 ui-monospace, SFMono-Regular, Menlo, monospace !important; text-align: right !important; user-select: none !important; white-space: pre !important; overflow: hidden !important; }
.ayu-validator .ayu-source-row .widget-textarea { flex: 1 1 auto !important; min-width: 0 !important; border-radius: 0 6px 6px 0 !important; }
.ayu-validator .ayu-actions .widget-button { flex: 0 1 auto; min-width: 118px; }
.ayu-validator .widget-button,
.ayu-validator .widget-button button { border: 1px solid #a8323d !important; border-radius: 5px !important; background: #4a1820 !important; color: #ffe1e1 !important; font-weight: 600 !important; box-shadow: none !important; }
.ayu-validator .widget-button button:hover:not(:disabled) { border-color: #ff6b6b !important; color: #ff9d9d !important; }
.ayu-validator .widget-button.mod-primary,
.ayu-validator .widget-button.mod-primary button { background: #e5484d !important; border-color: #ff6b6b !important; color: #22090c !important; }
.ayu-validator .ayu-status { margin: 12px 0 18px; color: #bfbdb6; font-size: 13px; }
.ayu-validator .ayu-table { max-height: 180px !important; border: 1px solid #2d333b; border-radius: 6px; overflow: auto !important; }
.ayu-validator table { width: 100%; border-collapse: collapse; background: #241014 !important; color: #f2d9d9 !important; font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace; }
.ayu-validator th { background: #681f2a !important; color: #ffd1d1 !important; font-weight: 700; text-align: left; }
.ayu-validator td, .ayu-validator th { border-bottom: 1px solid #5a2228; padding: 9px 10px; }
.ayu-validator .ayu-empty { color: #b98f93; padding: 12px 0; font-size: 13px; }
</style>
""")
        self._theme.add_class("ayu-theme")
        self.line_numbers.add_class("ayu-line-numbers")
        self._header = widgets.HTML("<div class='ayu-header'><div class='ayu-kicker'>POWER APPS / SOURCE TOOLING</div><h2>YAML Validator</h2><div class='ayu-subtitle'>Inspect, preview, and apply conservative source repairs.</div></div>")
        self._source_label = widgets.HTML("<div class='ayu-label'>SOURCE DOCUMENT</div>")
        self._diagnostics_label = widgets.HTML("<div class='ayu-label'>DIAGNOSTICS</div>")
        self._preview_label = widgets.HTML("<div class='ayu-label'>CORRECTED PREVIEW</div>")
        self.status.add_class("ayu-status")
        self.diagnostics_view.add_class("ayu-table")
        self.preview.add_class("ayu-preview")
        source_row = widgets.HBox([self.line_numbers, self.source])
        source_row.add_class("ayu-source-row")
        actions = widgets.HBox([self.validate_button, self.preview_button, self.apply_button, self.copy_button])
        actions.add_class("ayu-actions")
        actions.layout = widgets.Layout(width="100%", min_height="58px", overflow="visible")
        self.diagnostics_view.layout = widgets.Layout(width="100%", max_height="180px")
        self.preview.layout = widgets.Layout(width="100%", height="190px")
        self.widget = widgets.VBox([
            actions, self._theme, self._header, self._source_label, source_row,
            self.status, self._diagnostics_label, self.diagnostics_view,
            self._preview_label, self.preview,
        ], layout=widgets.Layout(width="100%", padding="0"))
        self.widget.add_class("ayu-validator")
        if initial_yaml.strip():
            self.validate()

    def _update_line_numbers(self) -> None:
        """Keep the non-editable line-number gutter synchronized with the source."""
        count = max(1, self.source.value.count("\n") + 1)
        self.line_numbers.value = "<br>".join(str(number) for number in range(1, count + 1))

    def validate(self) -> list[Diagnostic]:
        self._diagnostics = validate_text(self.source.value, self.source_name)
        self._fixes = propose_fixes(self.source.value, self._diagnostics)
        self._source_snapshot = self.source.value
        self.preview_button.disabled = not self._fixes
        self.apply_button.disabled = not self._fixes
        self.preview.value, self.copy_button.disabled = "", True
        errors = sum(item.severity == Severity.ERROR for item in self._diagnostics)
        warnings = len(self._diagnostics) - errors
        self.status.value = f"<b>{errors} error(s), {warnings} warning(s), {len(self._fixes)} safe fix(es).</b>"
        rows = "".join(f"<tr><td>{item.line}:{item.column}</td><td>{html.escape(item.severity.value)}</td><td>{html.escape(item.code)}</td><td>{html.escape(item.message)}</td></tr>" for item in self._diagnostics)
        self.diagnostics_view.value = f"<table><tr><th>Location</th><th>Severity</th><th>Code</th><th>Finding</th></tr>{rows}</table>" if rows else "<b>No issues found.</b>"
        return self._diagnostics

    def preview_selected(self) -> str:
        if self._source_snapshot != self.source.value:
            self.validate()
            return ""
        result = apply_fixes(self.source.value, self._fixes)
        self.preview.value, self.copy_button.disabled = result.text, not bool(result.text)
        return result.text

    def apply_selected(self) -> str:
        if self._source_snapshot != self.source.value:
            self.validate()
            return self.source.value
        result = apply_fixes(self.source.value, self._fixes)
        self.source.value = result.text
        self.validate()
        self.preview.value, self.copy_button.disabled = result.text, False
        if self.on_apply:
            self.on_apply(result.text)
        return result.text

    def copy_preview(self) -> None:
        text = self.preview.value or self.source.value
        display(Javascript(f"navigator.clipboard?.writeText({json.dumps(text)})"))
        self.status.value = "<b>Preview sent to the clipboard.</b>"

    def _source_changed(self, _: dict[str, Any]) -> None:
        self._update_line_numbers()
        if self._source_snapshot != self.source.value:
            self.preview_button.disabled = True
            self.apply_button.disabled = True
            self.copy_button.disabled = True
            self.preview.value = ""
            self.status.value = "<b>Source changed.</b> Validate again to refresh safe fixes."

    def display(self) -> "PowerAppsYamlValidatorUI":
        display(self.widget)
        return self


def create_validator_ui(initial_yaml: str = "", *, source_name: str = "canvas.pa.yaml", on_apply: Callable[[str], None] | None = None) -> PowerAppsYamlValidatorUI:
    return PowerAppsYamlValidatorUI(initial_yaml, source_name=source_name, on_apply=on_apply).display()