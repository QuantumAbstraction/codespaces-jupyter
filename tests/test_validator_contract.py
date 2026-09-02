import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from powerapps_validator import (
    Severity,
    ValidationController,
    apply_fixes,
    document_revision,
    propose_fixes,
    validate_text,
)
from powerapps_yaml_validator import PowerAppsYamlValidatorUI


FIXTURES = json.loads((Path(__file__).parents[1] / "powerapps_validator" / "fixtures.json").read_text())


def test_catalog_fixtures_have_stable_codes():
    for case in FIXTURES["cases"]:
        assert {item.code for item in validate_text(case["source"])} >= set(case["codes"])


def test_diagnostic_has_exact_range_and_documentation():
    source = 'Screens:\n  Home:\n    Children:\n      - save:\n          Control: ModernButton\n'
    diagnostic = next(item for item in validate_text(source) if item.code == "PAC100")
    assert diagnostic.severity is Severity.WARNING
    assert diagnostic.range.start.line == 5
    assert diagnostic.range.start.column == 20
    assert diagnostic.range.end.line == 5
    assert diagnostic.range.end.column > diagnostic.range.start.column
    assert diagnostic.documentation_url


def test_fixes_are_revision_guarded_and_previewable():
    source = 'Screens:\n  Home:\n    Children:\n      - save:\n          Control: ModernButton\n'
    fixes = propose_fixes(source)
    assert fixes[0].revision == document_revision(source)
    preview = apply_fixes(source, fixes)
    assert not preview.stale
    assert "ModernButton@1.0.0" in preview.text
    stale = apply_fixes(source + "# changed\n", fixes)
    assert stale.stale and not stale.applied


def test_controller_never_silently_applies_without_preview_contract():
    source = 'Screens:\n  Home:\n    Children:\n      - save:\n          Control: ModernButton\n'
    controller = ValidationController(source)
    controller.validate()
    preview = controller.preview()
    assert "ModernButton@1.0.0" in preview.text
    applied = controller.apply()
    assert "ModernButton@1.0.0" in applied.text
    assert controller.text == applied.text


def test_ui_toolbar_is_first_and_actions_follow_source_changes():
    source = 'Screens:\n  Home:\n    Children:\n      - save:\n          Control: ModernButton\n'
    ui = PowerAppsYamlValidatorUI(source)
    assert ui.widget.children[0] is not None
    assert ui.validate_button.disabled is False
    assert ui.preview_button.disabled is False
    assert ui.apply_button.disabled is False
    ui.source.value += "\n"
    assert ui.preview_button.disabled is True
    assert ui.apply_button.disabled is True
    assert ui.copy_button.disabled is True
    assert ui.preview.value == ""
    ui.validate_button.click()
    assert ui.preview_button.disabled is False
    ui.preview_button.click()
    assert "ModernButton@1.0.0" in ui.preview.value
