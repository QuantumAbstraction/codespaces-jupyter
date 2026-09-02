"""Notebook-friendly validation workspace for Power Apps YAML."""
from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import ipywidgets as widgets
from IPython.display import Javascript, display

from powerapps_validator import (
    Diagnostic,
    FixApplication,
    FixSuggestion,
    Severity,
    apply_fixes,
    diagnostic_id,
    document_revision,
    propose_fixes,
    validate_text,
)


@dataclass
class _UiState:
    filter_mode: str = "all"
    selected_fix_ids: set[str] = field(default_factory=set)
    expanded_why: set[str] = field(default_factory=set)
    active_line: int | None = None


class PowerAppsYamlValidatorUI:
    """Fluent-inspired offline Jupyter workspace for Power Apps YAML validation."""

    def __init__(
        self,
        initial_yaml: str = "",
        *,
        source_name: str = "canvas.pa.yaml",
        on_apply: Callable[[str], None] | None = None,
    ) -> None:
        self.source_name = source_name
        self.on_apply = on_apply
        self._state = _UiState()
        self._diagnostics: list[Diagnostic] = []
        self._fixes: list[FixSuggestion] = []
        self._fix_by_diag: dict[str, FixSuggestion] = {}
        self._source_snapshot: str | None = None
        self._revision = document_revision(initial_yaml)

        self._build_widgets(initial_yaml)
        self._wire_events()
        if initial_yaml.strip():
            self.validate()

    def _build_widgets(self, initial_yaml: str) -> None:
        self.source = widgets.Textarea(
            value=initial_yaml,
            layout=widgets.Layout(width="100%", height="320px"),
        )
        self.line_numbers = widgets.HTML()
        self.source_wrap = widgets.HBox([self.line_numbers, self.source])
        self.source_wrap.add_class("pav-source-row")

        self.validate_button = widgets.Button(description="Validate source", icon="check", button_style="primary")
        self.preview_button = widgets.Button(description="Preview repairs", icon="eye", disabled=True)
        self.apply_button = widgets.Button(description="Apply safe repairs", icon="check-circle", disabled=True)
        self.apply_all_button = widgets.Button(description="Apply all safe", icon="magic", disabled=True)
        self.copy_button = widgets.Button(description="Copy repaired YAML", icon="copy", disabled=True)
        self.download_button = widgets.Button(description="Download", icon="download", disabled=True)
        self.file_upload = widgets.FileUpload(accept=".yaml,.yml,.pa.yaml", multiple=False)

        self.filter_all = widgets.ToggleButton(value=True, description="All", layout=widgets.Layout(width="auto"))
        self.filter_errors = widgets.ToggleButton(value=False, description="Errors", layout=widgets.Layout(width="auto"))
        self.filter_warnings = widgets.ToggleButton(value=False, description="Warnings", layout=widgets.Layout(width="auto"))
        self.filter_fixable = widgets.ToggleButton(value=False, description="Fixable", layout=widgets.Layout(width="auto"))

        self.status = widgets.HTML()
        self.problems_view = widgets.HTML()
        self.diff_view = widgets.HTML()
        self.diff_toggle = widgets.ToggleButtons(
            options=[("Hide diff", False), ("Show diff", True)],
            value=False,
            style={"button_width": "120px"},
        )

        self._theme = widgets.HTML(self._theme_css())
        self._header = widgets.HTML(
            "<div class='pav-header'>"
            "<div class='pav-kicker'>Power Apps / Source tooling</div>"
            "<h2>YAML Validator</h2>"
            "<p class='pav-subtitle'>Studio-style diagnostics, selective repairs, and revision-safe previews.</p>"
            "</div>"
        )
        self._coverage = widgets.HTML(
            "<p class='pav-coverage'>Catalogued controls receive PA2108 property checks. "
            "Unknown controls (e.g. CanvasComponent) skip the matrix to avoid false positives.</p>"
        )

        toolbar = widgets.HBox(
            [
                self.validate_button,
                self.preview_button,
                self.apply_button,
                self.apply_all_button,
                self.copy_button,
                self.download_button,
                self.file_upload,
            ]
        )
        toolbar.add_class("pav-toolbar")

        filters = widgets.HBox([self.filter_all, self.filter_errors, self.filter_warnings, self.filter_fixable])
        filters.add_class("pav-filters")

        source_panel = widgets.VBox(
            [
                widgets.HTML("<div class='pav-panel-title'>Source document</div>"),
                self.source_wrap,
            ]
        )
        source_panel.add_class("pav-panel")

        problems_panel = widgets.VBox(
            [
                widgets.HTML("<div class='pav-panel-title'>Problems</div>"),
                filters,
                self.problems_view,
            ]
        )
        problems_panel.add_class("pav-panel")

        split = widgets.HBox([source_panel, problems_panel])
        split.add_class("pav-split")

        self.widget = widgets.VBox(
            [
                self._theme,
                self._header,
                self._coverage,
                toolbar,
                self.status,
                split,
                self.diff_toggle,
                self.diff_view,
            ],
            layout=widgets.Layout(width="100%"),
        )
        self.widget.add_class("pav-workspace")
        self._update_line_numbers()
        self._render_problems()
        self.diff_view.layout = widgets.Layout(width="100%")

    def _theme_css(self) -> str:
        return """
<style>
.pav-workspace {
  color-scheme: light;
  --pav-bg: #f5f5f5;
  --pav-surface: #ffffff;
  --pav-surface-hover: #f0f6fc;
  --pav-border: #8a8886;
  --pav-border-subtle: #d1d1d1;
  --pav-text: #161616;
  --pav-muted: #4a4a4a;
  --pav-primary: #0f6cbd;
  --pav-primary-hover: #115ea3;
  --pav-primary-text: #ffffff;
  --pav-error: #b10e1c;
  --pav-warning: #765000;
  --pav-info: #005a9e;
  --pav-success: #0b6a0b;
  --pav-code-bg: #f7f7f7;
  --pav-focus: #0f6cbd;
  --pav-disabled-bg: #e6e6e6;
  --pav-disabled-text: #6b6b6b;
  width: min(100% - 24px, 1280px) !important;
  max-height: min(780px, calc(100vh - 32px)) !important;
  margin: 12px auto !important;
  padding: 20px 24px !important;
  border: 1px solid var(--pav-border) !important;
  border-radius: 8px !important;
  background: var(--pav-bg) !important;
  color: var(--pav-text) !important;
  font-family: "Segoe UI", -apple-system, BlinkMacSystemFont, sans-serif !important;
  box-sizing: border-box !important;
  overflow-x: hidden !important;
  overflow-y: auto !important;
}
@media (prefers-color-scheme: dark) {
  .pav-workspace {
    color-scheme: dark;
    --pav-bg: #181818;
    --pav-surface: #242424;
    --pav-surface-hover: #233b50;
    --pav-border: #8a8a8a;
    --pav-border-subtle: #505050;
    --pav-text: #ffffff;
    --pav-muted: #d6d6d6;
    --pav-primary: #75b6e7;
    --pav-primary-hover: #a7d8f5;
    --pav-primary-text: #101010;
    --pav-error: #ff99a4;
    --pav-warning: #f7c948;
    --pav-info: #75b6e7;
    --pav-success: #89d185;
    --pav-code-bg: #111111;
    --pav-focus: #75b6e7;
    --pav-disabled-bg: #363636;
    --pav-disabled-text: #a0a0a0;
  }
}
.pav-workspace,
.pav-workspace .widget-label,
.pav-workspace .widget-html-content,
.pav-workspace table { color: var(--pav-text) !important; }
.pav-header { margin-bottom: 12px; }
.pav-kicker { color: var(--pav-primary); font-size: 11px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; }
.pav-header h2 { margin: 4px 0; font-size: 24px; font-weight: 600; color: var(--pav-text); }
.pav-subtitle, .pav-coverage { color: var(--pav-muted); font-size: 13px; margin: 0 0 8px; }
.pav-toolbar { gap: 8px !important; flex-wrap: wrap !important; position: sticky !important; top: 0; z-index: 5; background: var(--pav-bg) !important; padding: 8px 0 !important; }
.pav-toolbar .widget-button button {
  border-radius: 4px !important;
  border: 1px solid var(--pav-border) !important;
  background: var(--pav-surface) !important;
  color: var(--pav-text) !important;
  font-weight: 600 !important;
  min-height: 32px !important;
}
.pav-toolbar .widget-button button:hover:not(:disabled) {
  background: var(--pav-surface-hover) !important;
  border-color: var(--pav-primary) !important;
}
.pav-toolbar .widget-button.mod-primary button {
  background: var(--pav-primary) !important;
  border-color: var(--pav-primary) !important;
  color: var(--pav-primary-text) !important;
}
.pav-toolbar .widget-button.mod-primary button:hover:not(:disabled) {
  background: var(--pav-primary-hover) !important;
  border-color: var(--pav-primary-hover) !important;
}
.pav-toolbar .widget-button button:disabled {
  background: var(--pav-disabled-bg) !important;
  border-color: var(--pav-border-subtle) !important;
  color: var(--pav-disabled-text) !important;
  opacity: 1 !important;
}
.pav-toolbar .widget-button button:focus-visible { outline: 2px solid var(--pav-focus) !important; outline-offset: 2px !important; }
.pav-status { font-size: 13px; margin: 4px 0 12px; color: var(--pav-muted); }
.pav-split { width: 100% !important; height: 360px !important; min-height: 0 !important; gap: 16px !important; align-items: stretch !important; }
.pav-panel { flex: 1 1 50% !important; width: 50% !important; min-width: 0 !important; height: 360px !important; max-height: 360px !important; overflow: hidden !important; background: var(--pav-surface) !important; border: 1px solid var(--pav-border) !important; border-radius: 6px !important; padding: 12px !important; box-sizing: border-box !important; }
.pav-panel-title { font-size: 12px; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; color: var(--pav-muted); margin-bottom: 8px; }
.pav-source-row { align-items: stretch !important; gap: 0 !important; width: 100% !important; height: 320px !important; min-height: 0 !important; overflow: hidden !important; }
.pav-line-numbers {
  flex: 0 0 44px !important;
  width: 44px !important;
  height: 320px !important;
  max-height: 320px !important;
  padding: 10px 6px !important;
  background: var(--pav-code-bg) !important;
  border: 1px solid var(--pav-border) !important;
  border-right: 0 !important;
  border-radius: 4px 0 0 4px !important;
  font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace !important;
  color: var(--pav-muted) !important;
  text-align: right !important;
  overflow: hidden !important;
  user-select: none !important;
  box-sizing: border-box !important;
}
.pav-source-row > .widget-html { flex: 0 0 44px !important; width: 44px !important; height: 320px !important; max-height: 320px !important; overflow: hidden !important; }
.pav-source-row .widget-textarea { flex: 1 1 auto !important; min-width: 0 !important; width: calc(100% - 44px) !important; height: 320px !important; max-height: 320px !important; overflow: hidden !important; }
.pav-source-row .widget-textarea textarea {
  width: 100% !important;
  height: 320px !important;
  max-height: 320px !important;
  font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace !important;
  border-radius: 0 4px 4px 0 !important;
  border: 1px solid var(--pav-border) !important;
  background: var(--pav-code-bg) !important;
  color: var(--pav-text) !important;
  padding: 10px !important;
  resize: none !important;
  white-space: pre !important;
  overflow: auto !important;
  box-sizing: border-box !important;
}
.pav-source-row .widget-textarea textarea:focus-visible { outline: 2px solid var(--pav-focus) !important; outline-offset: 0 !important; }
.pav-filters { gap: 6px !important; margin-bottom: 8px !important; flex-wrap: wrap !important; }
.pav-filters .widget-toggle-button button,
.pav-workspace .widget-toggle-buttons .widget-toggle-button button {
  background: var(--pav-surface) !important;
  border-color: var(--pav-border) !important;
  color: var(--pav-text) !important;
}
.pav-filters .widget-toggle-button.mod-active button,
.pav-workspace .widget-toggle-buttons .widget-toggle-button.mod-active button {
  background: var(--pav-primary) !important;
  border-color: var(--pav-primary) !important;
  color: var(--pav-primary-text) !important;
}
.pav-filters .widget-toggle-button button:focus-visible,
.pav-workspace .widget-toggle-buttons .widget-toggle-button button:focus-visible {
  outline: 2px solid var(--pav-focus) !important;
  outline-offset: 2px !important;
}
.pav-workspace .widget-upload button {
  background: var(--pav-surface) !important;
  border: 1px solid var(--pav-border) !important;
  color: var(--pav-text) !important;
}
.pav-workspace input[type="checkbox"] { accent-color: var(--pav-primary); }
.pav-problems { height: 282px; max-height: 282px; overflow: auto; border: 1px solid var(--pav-border); border-radius: 4px; background: var(--pav-surface); }
.pav-problems table { width: 100%; table-layout: fixed; border-collapse: collapse; font-size: 12px; }
.pav-problems th { position: sticky; top: 0; z-index: 1; background: var(--pav-code-bg); color: var(--pav-text); text-align: left; padding: 8px; border-bottom: 1px solid var(--pav-border); }
.pav-problems th:nth-child(1), .pav-problems td:nth-child(1) { width: 24px; }
.pav-problems th:nth-child(2), .pav-problems td:nth-child(2) { width: 48px; }
.pav-problems th:nth-child(3), .pav-problems td:nth-child(3) { width: 58px; }
.pav-problems th:nth-child(4), .pav-problems td:nth-child(4) { width: 62px; }
.pav-problems th:nth-child(5), .pav-problems td:nth-child(5) { width: 110px; }
.pav-problems td { padding: 8px; color: var(--pav-text); border-bottom: 1px solid var(--pav-border-subtle); vertical-align: top; overflow-wrap: anywhere; word-break: break-word; }
.pav-problems td:last-child { max-height: 58px; overflow: hidden; }
.pav-problems tbody tr:hover td { background: var(--pav-surface-hover); }
.pav-problems tr.pav-active td { background: var(--pav-surface-hover); }
.pav-sev-error { color: var(--pav-error); font-weight: 700; }
.pav-sev-warning { color: var(--pav-warning); font-weight: 700; }
.pav-sev-info { color: var(--pav-info); font-weight: 700; }
.pav-why { color: var(--pav-muted); font-size: 11px; margin-top: 4px; max-height: 34px; overflow: auto; }
.pav-empty { padding: 16px; color: var(--pav-muted); font-size: 13px; }
.pav-diff { margin-top: 8px; border: 1px solid var(--pav-border); border-radius: 4px; background: var(--pav-code-bg); padding: 10px; font: 12px/1.45 ui-monospace, Menlo, monospace; max-height: 180px; overflow: auto; }
.pav-diff-del { color: var(--pav-error); }
.pav-diff-add { color: var(--pav-success); }
.pav-row-action { font-size: 11px; color: var(--pav-primary); cursor: pointer; border: 0; background: none; padding: 0; text-decoration: underline; }
.pav-row-action:hover { color: var(--pav-primary-hover); }
.pav-row-action:focus-visible { outline: 2px solid var(--pav-focus); outline-offset: 2px; }
@media (max-width: 900px) {
  .pav-workspace { max-height: none !important; overflow: visible !important; }
  .pav-split { height: auto !important; flex-direction: column !important; }
  .pav-panel { width: 100% !important; flex-basis: auto !important; }
}
</style>
<script>
(function() {
  function bindScrollSync() {
    document.querySelectorAll('.pav-source-row').forEach(function(row) {
      var gutter = row.querySelector('.pav-line-numbers');
      var textarea = row.querySelector('textarea');
      if (!gutter || !textarea || textarea.dataset.pavBound) return;
      textarea.dataset.pavBound = '1';
      textarea.addEventListener('scroll', function() { gutter.scrollTop = textarea.scrollTop; });
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bindScrollSync);
  else bindScrollSync();
  setTimeout(bindScrollSync, 500);
  setTimeout(bindScrollSync, 1500);
})();
</script>
"""

    def _wire_events(self) -> None:
        self.validate_button.on_click(lambda _: self.validate())
        self.preview_button.on_click(lambda _: self.preview_selected())
        self.apply_button.on_click(lambda _: self.apply_selected())
        self.apply_all_button.on_click(lambda _: self.apply_all())
        self.copy_button.on_click(lambda _: self.copy_preview())
        self.download_button.on_click(lambda _: self.download_preview())
        self.source.observe(self._source_changed, names="value")
        self.diff_toggle.observe(self._diff_toggle_changed, names="value")
        self.file_upload.observe(self._file_uploaded, names="value")

        for button, mode in (
            (self.filter_all, "all"),
            (self.filter_errors, "errors"),
            (self.filter_warnings, "warnings"),
            (self.filter_fixable, "fixable"),
        ):
            button.observe(lambda change, m=mode: self._set_filter(m), names="value")

    def _set_filter(self, mode: str) -> None:
        if not getattr(self, f"filter_{mode if mode != 'all' else 'all'}").value:
            return
        for other, name in (
            (self.filter_all, "all"),
            (self.filter_errors, "errors"),
            (self.filter_warnings, "warnings"),
            (self.filter_fixable, "fixable"),
        ):
            if name != mode:
                other.value = False
        if mode != "all" and not any(
            b.value for b in (self.filter_errors, self.filter_warnings, self.filter_fixable)
        ):
            self.filter_all.value = True
            mode = "all"
        self._state.filter_mode = mode
        self._render_problems()

    def _file_uploaded(self, change: dict[str, Any]) -> None:
        content = change["new"]
        if not content:
            return
        item = next(iter(content.values()))
        self.source.value = item["content"].decode("utf-8")
        name = item.get("metadata", {}).get("name")
        if name:
            self.source_name = name
        self.validate()

    def _diff_toggle_changed(self, change: dict[str, Any]) -> None:
        if change["new"]:
            self.preview_selected()
        else:
            self.diff_view.value = ""

    def _update_line_numbers(self) -> None:
        lines = self.source.value.splitlines() or [""]
        active = self._state.active_line
        rendered = []
        for index, _ in enumerate(lines, start=1):
            cls = "pav-line-active" if index == active else ""
            style = "font-weight:700;color:var(--pav-primary);" if index == active else ""
            rendered.append(f"<div class='{cls}' style='{style}'>{index}</div>")
        self.line_numbers.value = f"<div class='pav-line-numbers'>{''.join(rendered)}</div>"

    def _filtered_diagnostics(self) -> list[Diagnostic]:
        mode = self._state.filter_mode
        if mode == "errors":
            return [d for d in self._diagnostics if d.severity is Severity.ERROR]
        if mode == "warnings":
            return [d for d in self._diagnostics if d.severity is Severity.WARNING]
        if mode == "fixable":
            fixable_ids = set(self._fix_by_diag.keys())
            return [d for d in self._diagnostics if diagnostic_id(d) in fixable_ids]
        return list(self._diagnostics)

    def _render_problems(self) -> None:
        rows = []
        for diagnostic in self._filtered_diagnostics():
            diag_key = diagnostic_id(diagnostic)
            fix = self._fix_by_diag.get(diag_key)
            checked = "checked" if diag_key in self._state.selected_fix_ids else ""
            checkbox = f"<input type='checkbox' data-fix-id='{html.escape(diag_key)}' {checked} disabled>" if not fix else (
                f"<input type='checkbox' class='pav-fix-check' data-fix-id='{html.escape(diag_key)}' {checked}>"
            )
            sev_class = f"pav-sev-{diagnostic.severity.value}"
            control = html.escape(diagnostic.control_type or "—")
            why_block = ""
            if diag_key in self._state.expanded_why and diagnostic.why:
                why_block = f"<div class='pav-why'>{html.escape(diagnostic.why)}</div>"
            action = ""
            if fix:
                action = (
                    f"<button class='pav-row-action' data-action='toggle-fix' data-fix-id='{html.escape(diag_key)}'>"
                    f"{html.escape(fix.title)}</button>"
                )
            expand = ""
            if diagnostic.why:
                label = "Hide why" if diag_key in self._state.expanded_why else "Why?"
                expand = f" <button class='pav-row-action' data-action='toggle-why' data-diag-id='{html.escape(diag_key)}'>{label}</button>"
            active = "pav-active" if diagnostic.line == self._state.active_line else ""
            rows.append(
                f"<tr class='{active}' data-line='{diagnostic.line}'>"
                f"<td>{checkbox}</td>"
                f"<td>{diagnostic.line}:{diagnostic.column}</td>"
                f"<td class='{sev_class}'>{html.escape(diagnostic.severity.value)}</td>"
                f"<td>{html.escape(diagnostic.code)}</td>"
                f"<td>{control}</td>"
                f"<td>{html.escape(diagnostic.message)}{expand}{why_block}{action}</td>"
                f"</tr>"
            )
        if rows:
            table = (
                "<div class='pav-problems'><table>"
                "<tr><th></th><th>Loc</th><th>Sev</th><th>Code</th><th>Control</th><th>Finding</th></tr>"
                f"{''.join(rows)}</table></div>"
            )
        else:
            table = "<div class='pav-empty'>No issues match this filter.</div>"
        self.problems_view.value = table
        display(
            Javascript(
                """
(function() {
  const root = document.querySelector('.pav-problems');
  if (!root) return;
  root.querySelectorAll('.pav-fix-check').forEach(function(el) {
    el.onchange = function() {
      const id = el.getAttribute('data-fix-id');
      const checked = el.checked;
      if (window.pavFixToggle) window.pavFixToggle(id, checked);
    };
  });
  root.querySelectorAll('[data-action="toggle-why"]').forEach(function(el) {
    el.onclick = function(e) {
      e.preventDefault();
      if (window.pavWhyToggle) window.pavWhyToggle(el.getAttribute('data-diag-id'));
    };
  });
  root.querySelectorAll('[data-action="toggle-fix"]').forEach(function(el) {
    el.onclick = function(e) {
      e.preventDefault();
      if (window.pavFixToggle) window.pavFixToggle(el.getAttribute('data-fix-id'), true);
    };
  });
  root.querySelectorAll('tr[data-line]').forEach(function(row) {
    row.onclick = function(e) {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'BUTTON') return;
      if (window.pavJumpLine) window.pavJumpLine(parseInt(row.getAttribute('data-line'), 10));
    };
  });
})();
"""
            )
        )

    def _register_js_callbacks(self) -> None:
        selected = json.dumps(sorted(self._state.selected_fix_ids))
        display(
            Javascript(
                f"""
window.pavFixToggle = function(id, checked) {{
  if (!window._pavSelected) window._pavSelected = new Set({selected});
  if (checked) window._pavSelected.add(id); else window._pavSelected.delete(id);
}};
window.pavWhyToggle = function(id) {{
  if (!window._pavWhy) window._pavWhy = new Set();
  if (window._pavWhy.has(id)) window._pavWhy.delete(id); else window._pavWhy.add(id);
}};
window.pavJumpLine = function(line) {{
  const ta = document.querySelector('.pav-source-row textarea');
  if (!ta) return;
  const lines = ta.value.split('\\n');
  let pos = 0;
  for (let i = 0; i < line - 1 && i < lines.length; i++) pos += lines[i].length + 1;
  ta.focus();
  ta.setSelectionRange(pos, pos);
  ta.scrollTop = Math.max(0, (line - 4) * 18);
}};
"""
            )
        )

    def _sync_js_state(self) -> None:
        if hasattr(self, "_js_selected"):
            self._state.selected_fix_ids = set(getattr(self, "_js_selected", self._state.selected_fix_ids))
        display(
            Javascript(
                """
if (window._pavSelected) {
  window._pavSelectedIds = Array.from(window._pavSelected);
}
if (window._pavWhy) {
  window._pavWhyIds = Array.from(window._pavWhy);
}
"""
            )
        )

    def _selected_fixes(self) -> list[FixSuggestion]:
        selected = self._state.selected_fix_ids or {f.diagnostic_id for f in self._fixes if f.diagnostic_id}
        if not self._state.selected_fix_ids and self._fixes:
            selected = {f.diagnostic_id for f in self._fixes if f.diagnostic_id}
        return [f for f in self._fixes if f.diagnostic_id in selected]

    def _update_status(self) -> None:
        errors = sum(1 for item in self._diagnostics if item.severity is Severity.ERROR)
        warnings = sum(1 for item in self._diagnostics if item.severity is Severity.WARNING)
        info = len(self._diagnostics) - errors - warnings
        fixable = len(self._fixes)
        self.status.value = (
            f"<div class='pav-status'><b>{errors}</b> error(s), "
            f"<b>{warnings}</b> warning(s), <b>{info}</b> info, "
            f"<b>{fixable}</b> safe repair(s).</div>"
        )
        has_fixes = bool(self._fixes)
        stale = self._source_snapshot != self.source.value
        disabled = not has_fixes or stale
        self.preview_button.disabled = disabled
        self.apply_button.disabled = disabled
        self.apply_all_button.disabled = disabled
        self.copy_button.disabled = stale
        self.download_button.disabled = stale

    def _build_diff(self, original: str, fixes: Sequence[FixSuggestion]) -> str:
        if not fixes:
            return ""
        lines = original.splitlines(keepends=True)
        chunks: list[str] = []
        for fix in sorted(fixes, key=lambda item: item.edit.start.line):
            start = fix.edit.start.line
            end = fix.edit.end.line
            before_lines = lines[start - 1 : end] if start <= len(lines) else []
            before = "".join(before_lines).rstrip("\n")
            after = fix.edit.replacement.rstrip("\n")
            if before:
                chunks.append(f"<div class='pav-diff-del'>- {html.escape(before)}</div>")
            if after:
                chunks.append(f"<div class='pav-diff-add'>+ {html.escape(after)}</div>")
        return f"<div class='pav-diff'>{''.join(chunks)}</div>" if chunks else ""

    def validate(self) -> list[Diagnostic]:
        self._revision = document_revision(self.source.value)
        self._diagnostics = validate_text(self.source.value, self.source_name)
        self._fixes = propose_fixes(self.source.value, self._diagnostics)
        self._fix_by_diag = {f.diagnostic_id: f for f in self._fixes if f.diagnostic_id}
        self._source_snapshot = self.source.value
        self._state.selected_fix_ids = set(self._fix_by_diag.keys())
        self._update_line_numbers()
        self._update_status()
        self._render_problems()
        self._register_js_callbacks()
        if self.diff_toggle.value:
            self.preview_selected()
        else:
            self.diff_view.value = ""
        return self._diagnostics

    def preview_selected(self, fixes: Sequence[FixSuggestion] | None = None) -> str:
        if self._source_snapshot != self.source.value:
            self.validate()
            return ""
        chosen = list(fixes) if fixes is not None else self._selected_fixes()
        result = apply_fixes(self.source.value, chosen, expected_revision=self._revision)
        self.diff_view.value = self._build_diff(self.source.value, chosen)
        if result.stale:
            self.status.value = "<div class='pav-status'><b>Source changed.</b> Validate again before applying repairs.</div>"
            return self.source.value
        self.copy_button.disabled = False
        self.download_button.disabled = False
        self._preview_text = result.text
        return result.text

    def apply_selected(self) -> str:
        if self._source_snapshot != self.source.value:
            self.validate()
            return self.source.value
        chosen = self._selected_fixes()
        result = apply_fixes(self.source.value, chosen, expected_revision=self._revision)
        if result.stale:
            self.status.value = "<div class='pav-status'><b>Repairs are stale.</b> Validate again.</div>"
            return self.source.value
        self.source.value = result.text
        if self.on_apply:
            self.on_apply(result.text)
        self.validate()
        return self.source.value

    def apply_all(self) -> str:
        self._state.selected_fix_ids = {f.diagnostic_id for f in self._fixes if f.diagnostic_id}
        return self.apply_selected()

    def copy_preview(self) -> None:
        text = getattr(self, "_preview_text", None) or self.source.value
        display(Javascript(f"navigator.clipboard?.writeText({json.dumps(text)})"))
        self.status.value = "<div class='pav-status'><b>Repaired YAML copied to clipboard.</b></div>"

    def download_preview(self) -> None:
        text = getattr(self, "_preview_text", None) or self.source.value
        display(
            Javascript(
                f"""
(function() {{
  const blob = new Blob([{json.dumps(text)}], {{type: 'text/yaml'}});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = {json.dumps(self.source_name)};
  a.click();
  URL.revokeObjectURL(url);
}})();
"""
            )
        )

    def _source_changed(self, _: dict[str, Any]) -> None:
        self._update_line_numbers()
        if self._source_snapshot != self.source.value:
            self.preview_button.disabled = True
            self.apply_button.disabled = True
            self.apply_all_button.disabled = True
            self.copy_button.disabled = True
            self.download_button.disabled = True
            self.diff_view.value = ""
            self.status.value = "<div class='pav-status'><b>Source changed.</b> Validate again to refresh safe repairs.</div>"

    def display(self) -> "PowerAppsYamlValidatorUI":
        display(self.widget)
        return self


def create_validator_ui(
    initial_yaml: str = "",
    *,
    source_name: str = "canvas.pa.yaml",
    on_apply: Callable[[str], None] | None = None,
) -> PowerAppsYamlValidatorUI:
    return PowerAppsYamlValidatorUI(initial_yaml, source_name=source_name, on_apply=on_apply).display()
