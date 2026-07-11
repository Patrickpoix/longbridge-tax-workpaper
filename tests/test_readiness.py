from datetime import date
from pathlib import Path

from longbridge_tax_workpaper.config import prepare_runtime_config, runtime_config_environment
from longbridge_tax_workpaper.cost_basis import MethodResult
from longbridge_tax_workpaper.filing_readiness import assess_filing_readiness
from longbridge_tax_workpaper.schema import FieldValue, StatementResult


def minimal_report():
    return {
        "fifo": MethodResult(method="FIFO"),
        "moving_average": MethodResult(method="MOVING_AVERAGE"),
        "errors": [],
        "opening_lots": [],
        "prior_period_coverage": {
            "status": "ok",
            "actual_months": [],
            "expected_months": [],
            "event_count": 0,
            "monthly_reconciliation": [],
            "monthly_reconciliation_error_count": 0,
            "monthly_reconciliation_status": "not_applicable",
        },
    }


def test_explicit_partial_year_generates_but_review_is_blocked(tmp_path: Path):
    statements = [StatementResult("202601", "jan.pdf")]
    paths = prepare_runtime_config(tmp_path / "config", tax_year=2026, account_opening_month="202601", fx_rates={"USD": 7, "HKD": 0.9})
    with runtime_config_environment(paths):
        status = assess_filing_readiness(statements, cost_report=minimal_report(), as_of=date(2027, 1, 1))
    monthly = next(item for item in status["checks"] if item["code"] == "MONTHLY_COVERAGE")
    assert monthly["status"] == "BLOCKED"
    assert monthly["blocking"] is True
    assert status["status"] == "BLOCKED_FOR_REVIEW"
    assert status["ready_for_review"] is False
    assert status["ready_to_file"] is False


def test_complete_year_with_tax_warnings_is_review_required_not_ready(tmp_path: Path):
    statements = [StatementResult(f"2026{month:02d}", f"{month}.pdf") for month in range(1, 13)]
    paths = prepare_runtime_config(tmp_path / "config", tax_year=2026, account_opening_month="202601", fx_rates={"USD": 7, "HKD": 0.9})
    with runtime_config_environment(paths):
        status = assess_filing_readiness(statements, cost_report=minimal_report(), as_of=date(2027, 1, 1))
    assert status["status"] == "REVIEW_REQUIRED"
    assert status["ready_for_review"] is True
    assert status["ready_to_file"] is False
    assert any(item["risk_type"] == "tax_treatment" for item in status["checks"] if item["status"] == "WARNING")


def _complete_year_statements(year: int = 2026):
    return [StatementResult(f"{year}{month:02d}", f"{month}.pdf") for month in range(1, 13)]


def _with_template_meta(statement, *, template_id, source, score):
    statement.metadata["template_id"] = FieldValue.derived(template_id, confidence=0.95)
    statement.metadata["template_recognition_source"] = FieldValue.derived(source, confidence=1.0)
    statement.metadata["template_signature_score"] = FieldValue.derived(score, confidence=1.0)
    return statement


def test_native_high_confidence_templates_pass_recognition_check(tmp_path: Path):
    statements = [
        _with_template_meta(s, template_id="overview_headered", source="native_text", score=13)
        for s in _complete_year_statements()
    ]
    paths = prepare_runtime_config(tmp_path / "config", tax_year=2026, account_opening_month="202601", fx_rates={"USD": 7, "HKD": 0.9})
    with runtime_config_environment(paths):
        status = assess_filing_readiness(statements, cost_report=minimal_report(), as_of=date(2027, 1, 1))
    recognition = next(item for item in status["checks"] if item["code"] == "TEMPLATE_RECOGNITION")
    assert recognition["status"] == "PASS"
    assert recognition["blocking"] is False


def test_ocr_assisted_template_escalates_to_review(tmp_path: Path):
    statements = _complete_year_statements()
    _with_template_meta(statements[5], template_id="overview_headered", source="ocr_assisted", score=13)
    for other in statements[:5] + statements[6:]:
        _with_template_meta(other, template_id="overview_headered", source="native_text", score=13)
    paths = prepare_runtime_config(tmp_path / "config", tax_year=2026, account_opening_month="202601", fx_rates={"USD": 7, "HKD": 0.9})
    with runtime_config_environment(paths):
        status = assess_filing_readiness(statements, cost_report=minimal_report(), as_of=date(2027, 1, 1))
    recognition = next(item for item in status["checks"] if item["code"] == "TEMPLATE_RECOGNITION")
    assert recognition["status"] == "WARNING"
    assert "202606" in recognition["detail"]
    assert status["status"] == "REVIEW_REQUIRED"
    assert status["ready_for_review"] is True


def test_low_signature_score_escalates_to_review(tmp_path: Path):
    statements = [
        _with_template_meta(s, template_id="legacy_inline_overview", source="native_text", score=6)
        for s in _complete_year_statements()
    ]
    paths = prepare_runtime_config(tmp_path / "config", tax_year=2026, account_opening_month="202601", fx_rates={"USD": 7, "HKD": 0.9})
    with runtime_config_environment(paths):
        status = assess_filing_readiness(statements, cost_report=minimal_report(), as_of=date(2027, 1, 1))
    recognition = next(item for item in status["checks"] if item["code"] == "TEMPLATE_RECOGNITION")
    assert recognition["status"] == "WARNING"
    assert status["status"] == "REVIEW_REQUIRED"
