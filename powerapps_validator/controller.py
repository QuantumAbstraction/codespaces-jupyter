"""Host-neutral controller boundary for editors and notebooks."""
from __future__ import annotations
from dataclasses import dataclass
from .diagnostics import Diagnostic, FixApplication, FixSuggestion, document_revision
from .fixes import apply_fixes, propose_fixes
from .rules import validate_text

@dataclass
class ValidationController:
    text: str = ""
    source_name: str = "canvas.pa.yaml"
    revision: str = ""
    diagnostics: list[Diagnostic] | None = None
    fixes: list[FixSuggestion] | None = None

    def __post_init__(self) -> None:
        self.revision = document_revision(self.text)

    def validate(self) -> list[Diagnostic]:
        self.revision = document_revision(self.text)
        self.diagnostics = validate_text(self.text, self.source_name)
        self.fixes = propose_fixes(self.text, self.diagnostics)
        return self.diagnostics

    def preview(self, fixes: list[FixSuggestion] | None = None) -> FixApplication:
        if self.revision != document_revision(self.text):
            self.validate()
        chosen = fixes if fixes is not None else (self.fixes or [])
        return apply_fixes(self.text, chosen, expected_revision=self.revision)

    def apply(self, fixes: list[FixSuggestion] | None = None) -> FixApplication:
        result = self.preview(fixes)
        if result.stale:
            return result
        self.text = result.text
        self.validate()
        return result
