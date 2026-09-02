"""Power Apps YAML validation package."""
from .controller import ValidationController
from .diagnostics import (
    Diagnostic,
    FixAction,
    FixApplication,
    FixEdit,
    FixSuggestion,
    Severity,
    diagnostic_id,
    document_revision,
)
from .fixes import apply_fixes, propose_fixes
from .parser import ParsedDocument, parse_document
from .rules import validate_document, validate_text

__all__ = [
    "Diagnostic",
    "FixAction",
    "FixApplication",
    "FixEdit",
    "FixSuggestion",
    "Severity",
    "ParsedDocument",
    "ValidationController",
    "apply_fixes",
    "diagnostic_id",
    "document_revision",
    "parse_document",
    "propose_fixes",
    "validate_document",
    "validate_text",
]
