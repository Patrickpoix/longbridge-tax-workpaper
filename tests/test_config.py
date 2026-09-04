import json
from pathlib import Path

from longbridge_tax_workpaper.config import prepare_runtime_config


def test_runtime_config_is_year_parameterized(tmp_path: Path):
    paths = prepare_runtime_config(
        tmp_path,
        tax_year=2027,
        account_opening_month="202601",
        fx_rates={"USD": 7.1, "HKD": 0.91},
    )
    policy = json.loads(paths["policy"].read_text(encoding="utf-8"))
    profile = json.loads(paths["profile"].read_text(encoding="utf-8"))
    assert policy["tax_year"] == 2027
    assert policy["year_end_date"] == "2027-12-31"
    assert policy["year_end_fx_rates"]["USD"]["rate"] == "7.1"
    assert profile["tax_year"] == 2027
    assert profile["account_opening_month"] == "202601"


def test_missing_fx_is_null_and_evidence_metadata_is_preserved(tmp_path: Path):
    paths = prepare_runtime_config(
        tmp_path,
        tax_year=2028,
        account_opening_month="202801",
        fx_rates={"USD": 7.2},
        fx_metadata={
            "USD": {
                "source_status": "documented",
                "source_date": "2028-12-31",
                "source_url": "https://example.invalid/usd",
                "evidence_sha256": "a" * 64,
            }
        },
    )
    policy = json.loads(paths["policy"].read_text(encoding="utf-8"))
    assert policy["year_end_fx_rates"]["USD"]["rate"] == "7.2"
    assert policy["year_end_fx_rates"]["USD"]["source_status"] == "documented"
    assert policy["year_end_fx_rates"]["USD"]["source_url"] == "https://example.invalid/usd"
    assert policy["year_end_fx_rates"]["USD"]["evidence_sha256"] == "a" * 64
    assert policy["year_end_fx_rates"]["HKD"]["rate"] is None
    assert policy["year_end_fx_rates"]["HKD"]["source_status"] == "missing"


def test_runtime_tax_flags_are_requests_not_automatic_legal_conclusions(tmp_path: Path):
    paths = prepare_runtime_config(
        tmp_path,
        tax_year=2028,
        account_opening_month="202801",
        withholding_credit=True,
        deduct_margin_interest=True,
        cost_basis_method="FIFO",
    )
    policy = json.loads(paths["policy"].read_text(encoding="utf-8"))
    profile = json.loads(paths["profile"].read_text(encoding="utf-8"))
    dividend = policy["dividend_filing_basis"]
    margin = policy["category_rules"]["margin_interest_deductible"]
    assert dividend["apply_statement_withholding_credit"] is True
    assert dividend["automatic_credit_without_formal_documents"] == 0.0
    assert margin["deduction_requested"] is True
    assert margin["deductible_in_final_filing"] is False
    assert margin["treatment"] == "deduction_candidate_requires_review"
    assert profile["cost_basis_method"]["selected_method"] == "FIFO"
    assert profile["cost_basis_method"]["status"] == "user_selected"
    assert profile["cost_basis_method"]["methods_produced"] == ["FIFO", "MOVING_AVERAGE"]


def test_default_cost_method_is_moving_average_and_both_methods_are_retained(tmp_path: Path):
    paths = prepare_runtime_config(tmp_path, tax_year=2028, account_opening_month="202801")
    profile = json.loads(paths["profile"].read_text(encoding="utf-8"))
    method = profile["cost_basis_method"]
    assert method["selected_method"] == "MOVING_AVERAGE"
    assert method["status"] == "user_selected"
    assert method["methods_produced"] == ["FIFO", "MOVING_AVERAGE"]
