"""Stable source diagnostics and revision-safe edits."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class FixAction(str, Enum):
    DELETE = "delete"
    RENAME = "rename"
    REPLACE = "replace"
    PIN_VERSION = "pin_version"
    ADD_EQUALS = "add_equals"
    VARIANT_REWRITE = "variant_rewrite"


@dataclass(frozen=True)
class SourcePosition:
    line: int
    column: int


@dataclass(frozen=True)
class SourceRange:
    start: SourcePosition
    end: SourcePosition


@dataclass(frozen=True)
class FixEdit:
    start: SourcePosition
    end: SourcePosition
    replacement: str


@dataclass(frozen=True)
class Diagnostic:
    code: str
    severity: Severity
    message: str
    range: SourceRange = field(default_factory=lambda: SourceRange(SourcePosition(1, 1), SourcePosition(1, 1)))
    path: str = "$"
    documentation_url: str | None = None
    fix: FixEdit | None = None
    control_type: str | None = None
    property_name: str | None = None
    fix_action: FixAction | None = None
    fix_rename_to: str | None = None
    why: str | None = None

    @property
    def line(self) -> int:
        return self.range.start.line

    @property
    def column(self) -> int:
        return self.range.start.column

    def format(self, source_name: str = "<memory>") -> str:
        return f"{source_name}:{self.line}:{self.column}: {self.severity.value} {self.code}: {self.message} [{self.path}]"


@dataclass(frozen=True)
class FixSuggestion:
    code: str
    title: str
    message: str
    edit: FixEdit
    revision: str
    documentation_url: str | None = None
    diagnostic_id: str | None = None
    why: str | None = None

    @property
    def line(self) -> int:
        return self.edit.start.line

    @property
    def end_line(self) -> int:
        return self.edit.end.line

    @property
    def before(self) -> str:
        return ""

    @property
    def after(self) -> str:
        return self.edit.replacement


@dataclass(frozen=True)
class FixApplication:
    text: str
    applied: tuple[FixSuggestion, ...]
    skipped: tuple[FixSuggestion, ...]
    stale: bool = False


def document_revision(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def diagnostic_id(diagnostic: Diagnostic) -> str:
    return f"{diagnostic.code}:{diagnostic.path}:{diagnostic.range.start.line}:{diagnostic.range.start.column}"
