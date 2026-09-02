import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from powerapps_validator import (
    FixAction,
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
        codes = {item.code for item in validate_text(case["source"])}
        assert codes >= set(case["codes"])
        for forbidden in case.get("must_not_include", []):
            assert forbidden not in codes


def test_diagnostic_has_exact_range_and_documentation():
    source = 'Screens:\n  Home:\n    Children:\n      - save:\n          Control: ModernButton\n'
    diagnostic = next(item for item in validate_text(source) if item.code == "PAC100")
    assert diagnostic.severity is Severity.WARNING
    assert diagnostic.range.start.line == 5
    assert diagnostic.range.start.column == 20
    assert diagnostic.documentation_url


def test_label_radius_pa2108_delete():
    source = FIXTURES["cases"][2]["source"]
    diagnostic = next(item for item in validate_text(source) if item.code == "PA2108")
    assert "RadiusTopLeft" in diagnostic.message
    assert diagnostic.fix_action is FixAction.DELETE
    fixes = propose_fixes(source)
    result = apply_fixes(source, fixes)
    assert "RadiusTopLeft" not in result.text
    assert "Size: =16" in result.text


def test_label_fontsize_rename():
    source = FIXTURES["cases"][3]["source"]
    diagnostic = next(item for item in validate_text(source) if item.code == "PA2108")
    assert diagnostic.fix_rename_to == "Size"
    fixes = propose_fixes(source)
    result = apply_fixes(source, fixes)
    assert "FontSize:" not in result.text
    assert "Size: =16" in result.text


def test_multiline_unknown_property_deletes_block():
    source = """Screens:
  Home:
    Children:
      - lbl:
          Control: Label@2.5.1
          Properties:
            Text: |-
              =nfBi(
                  "Hello",
                  "Bonjour"
              )
            RadiusBottomLeft: =8
"""
    fixes = propose_fixes(source)
    radius_fix = next(f for f in fixes if "Radius" in f.title)
    result = apply_fixes(source, [radius_fix])
    assert "RadiusBottomLeft" not in result.text
    assert "nfBi(" in result.text


def test_inline_formula_with_yaml_colon_converts_to_literal_block():
    source = """Screens:
  Home:
    Children:
      - button:
          Control: Classic/Button@2.2.0
          Properties:
            OnSelect: =UpdateContext({selectedView: "REGISTRATIONS"})
"""
    diagnostics = validate_text(source)
    parse_error = next(item for item in diagnostics if item.code == "PAX001")
    assert parse_error.fix_action is FixAction.REPLACE
    fixes = propose_fixes(source, diagnostics)
    assert fixes[0].title == "Convert OnSelect to YAML block"
    result = apply_fixes(source, fixes)
    assert "OnSelect: |-" in result.text
    assert '  =UpdateContext({selectedView: "REGISTRATIONS"})' in result.text
    assert not any(item.code == "PAX001" for item in validate_text(result.text))


def test_groupcontainer_variant_rewrite():
    source = FIXTURES["cases"][4]["source"]
    assert any(item.code == "PA2109" for item in validate_text(source))
    fixes = propose_fixes(source)
    result = apply_fixes(source, fixes)
    assert "Variant: AutoLayout" in result.text
    assert "LayoutDirection: =LayoutDirection.Horizontal" in result.text


def test_unknown_control_skips_pa2108():
    source = FIXTURES["cases"][5]["source"]
    codes = {item.code for item in validate_text(source)}
    assert "PAC101" in codes
    assert "PA2108" not in codes


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


def test_ui_toolbar_and_source_change_guards():
    source = 'Screens:\n  Home:\n    Children:\n      - save:\n          Control: ModernButton\n'
    ui = PowerAppsYamlValidatorUI(source)
    assert ui.validate_button.disabled is False
    ui.validate()
    assert ui.preview_button.disabled is False
    ui.source.value += "\n"
    assert ui.preview_button.disabled is True
    assert ui.apply_button.disabled is True
    assert ui.copy_button.disabled is True
    ui.validate()
    assert ui.preview_button.disabled is False


def test_ui_selected_only_apply():
    source = """Screens:
  Home:
    Children:
      - a:
          Control: ModernButton
          Properties:
            Text: Save
      - b:
          Control: ModernButton
          Properties:
            Text: Cancel
"""
    ui = PowerAppsYamlValidatorUI(source)
    ui.validate()
    pin_fixes = [f for f in ui._fixes if f.code == "PAC100"]
    assert len(pin_fixes) == 2
    ui._state.selected_fix_ids = {pin_fixes[0].diagnostic_id}
    preview = ui.preview_selected([pin_fixes[0]])
    assert preview.count("ModernButton@1.0.0") == 1
