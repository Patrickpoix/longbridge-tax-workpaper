from longbridge_tax_workpaper.dividends import build_dividend_tax_basis_rows
from longbridge_tax_workpaper.margin_interest import (
    build_margin_interest_actual_payment_row,
    build_margin_interest_hkd_basis_row,
)
from longbridge_tax_workpaper.schema import FieldValue, SectionResult, StatementResult


def fv(value):
    return FieldValue.native(value, confidence=1.0)


def test_margin_interest_uses_same_month_pdf_rate():
    st = StatementResult("202601", "sample.pdf")
    st.sections["cash_balances"] = SectionResult("cash_balances", rows=[
        {"currency_label": fv("港元"), "accrued_interest": fv(-100.0)},
        {"currency_label": fv("美元"), "accrued_interest": fv(-2.0), "reference_rate": fv(7.8)},
        {"currency_label": fv("汇总(HKD)"), "accrued_interest": fv(-115.6)},
    ])
    row = build_margin_interest_hkd_basis_row(st)
    assert row["usd_interest_hkd_equivalent"] == -15.6
    assert row["total_margin_interest_hkd_tax_basis"] == -115.6
    assert row["validation_status"] == "ok"


def test_actual_margin_interest_uses_same_month_pdf_rate_and_keeps_currencies_separate():
    st = StatementResult("202601", "sample.pdf")
    st.sections["cash_balances"] = SectionResult("cash_balances", rows=[
        {"currency_label": fv("美元"), "reference_rate": fv(7.8499)},
    ])
    st.sections["other_fund_flows"] = SectionResult("other_fund_flows", rows=[
        {"tax_category": fv("margin_interest_deductible"), "currency": fv("HKD"), "cash_amount": fv(-132.99), "raw_detail": fv("融资利息 -132.99")},
        {"tax_category": fv("margin_interest_deductible"), "currency": fv("USD"), "cash_amount": fv(-0.17), "raw_detail": fv("融资利息 -0.17")},
    ])
    row = build_margin_interest_actual_payment_row(st)
    assert row["hkd_actual_payment"] == -132.99
    assert row["usd_actual_payment"] == -0.17
    assert row["usd_actual_payment_hkd_equivalent"] == -1.33
    assert row["total_actual_payment_hkd_equivalent"] == -134.32
    assert row["validation_status"] == "ok"


def test_embedded_dividend_withholding_is_separated():
    st = StatementResult("202601", "sample.pdf")
    st.sections["other_fund_flows"] = SectionResult("other_fund_flows", rows=[{
        "tax_category": fv("dividend_income"),
        "raw_detail": fv("#1288.HK DIVIDEND RMB0.20/SH Held:100 (- 2.00 10%)"),
        "currency": fv("HKD"), "cash_amount": fv(18.0), "date": fv("2026.01.15"),
    }])
    row = build_dividend_tax_basis_rows([st])[0]
    assert row["gross_dividend_cny"] == 20.0
    assert row["embedded_withholding_cny"] == 2.0
    assert row["automatic_credit_cny"] == 0.0
    assert row["statement_withholding_credit_candidate_cny"] == 2.0
