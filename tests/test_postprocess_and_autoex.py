from __future__ import annotations

from longbridge_tax_workpaper.cost_basis import build_cost_basis_events
from longbridge_tax_workpaper.postprocess import resolve_cross_month_statement_context
from longbridge_tax_workpaper.schema import FieldValue, SectionResult, StatementResult


def fv(value):
    return FieldValue.native(value, confidence=1.0)


def test_small_hkd_interest_is_not_misclassified_as_usd():
    previous = StatementResult("202412", "previous.pdf")
    previous.sections["cash_balances"] = SectionResult("cash_balances", rows=[
        {"currency_label": fv("港元"), "accrued_interest": fv(-5.00)},
        {"currency_label": fv("美元"), "accrued_interest": fv(-0.17)},
    ])
    current = StatementResult("202501", "current.pdf")
    current.sections["cash_balances"] = SectionResult("cash_balances", rows=[
        {"currency_label": fv("港元"), "accrued_interest": fv(-6.00)},
        {"currency_label": fv("美元"), "accrued_interest": fv(-0.20)},
    ])
    current.sections["other_fund_flows"] = SectionResult("other_fund_flows", rows=[
        {"tax_category": fv("margin_interest_deductible"), "currency": FieldValue.missing(), "cash_amount": fv(-5.00), "raw_detail": fv("贷款利息 -5.00")},
        {"tax_category": fv("margin_interest_deductible"), "currency": FieldValue.missing(), "cash_amount": fv(-0.17), "raw_detail": fv("贷款利息 -0.17")},
    ])
    resolve_cross_month_statement_context([previous, current])
    assert [row["currency"].value for row in current.sections["other_fund_flows"].rows] == ["HKD", "USD"]


def test_same_day_multiple_auto_ex_events_consume_rows_once():
    st = StatementResult("202511", "sample.pdf")
    rows = [
        {"date": fv("2025.11.27"), "tax_category": fv("derivative_auto_ex_proceeds"), "type": fv("company_action_cash_in"), "cash_amount": fv(100), "raw_detail": fv("#17986.HK AUTO-EX Held:100")},
        {"date": fv("2025.11.27"), "tax_category": fv("non_cash_company_action"), "type": fv("company_action_stock_out"), "quantity_change": fv(-100), "raw_detail": fv("#17986.HK AUTO-EX Held:100 stock out")},
        {"date": fv("2025.11.27"), "tax_category": fv("derivative_settlement_fee_deductible"), "type": fv("company_action_fee"), "cash_amount": fv(-1), "raw_detail": fv("#17986.HK Handling Fee")},
        {"date": fv("2025.11.27"), "tax_category": fv("derivative_auto_ex_proceeds"), "type": fv("company_action_cash_in"), "cash_amount": fv(250), "raw_detail": fv("#17986.HK AUTO-EX Held:200")},
        {"date": fv("2025.11.27"), "tax_category": fv("non_cash_company_action"), "type": fv("company_action_stock_out"), "quantity_change": fv(-200), "raw_detail": fv("#17986.HK AUTO-EX Held:200 stock out")},
        {"date": fv("2025.11.27"), "tax_category": fv("derivative_settlement_fee_deductible"), "type": fv("company_action_fee"), "cash_amount": fv(-2), "raw_detail": fv("#17986.HK Handling Fee")},
    ]
    st.sections["other_fund_flows"] = SectionResult("other_fund_flows", rows=rows)
    events = [event for event in build_cost_basis_events([st]) if event.source_reference.startswith("AUTOEX:")]
    assert len(events) == 2
    assert [(event.quantity, event.fees, event.cash_effect) for event in events] == [(100.0, 1.0, 99.0), (200.0, 2.0, 248.0)]
    assert len({event.source_reference for event in events}) == 2
    assert all("nearest_row" in event.evidence for event in events)


def test_unique_remaining_currency_resolves_mismatched_hkd_amount():
    previous = StatementResult("202505", "previous.pdf")
    previous.sections["cash_balances"] = SectionResult("cash_balances", rows=[
        {"currency_label": fv("港元"), "accrued_interest": fv(-128.70)},
        {"currency_label": fv("美元"), "accrued_interest": fv(-0.16)},
    ])
    current = StatementResult("202506", "current.pdf")
    current.sections["cash_balances"] = SectionResult("cash_balances", rows=[
        {"currency_label": fv("港元"), "accrued_interest": fv(-143.39)},
        {"currency_label": fv("美元"), "accrued_interest": fv(-4.68)},
    ])
    current.sections["other_fund_flows"] = SectionResult("other_fund_flows", rows=[
        {"tax_category": fv("margin_interest_deductible"), "currency": FieldValue.missing(), "cash_amount": fv(-132.99), "raw_detail": fv("融资利息 -132.99")},
        {"tax_category": fv("margin_interest_deductible"), "currency": fv("USD"), "cash_amount": fv(-0.17), "raw_detail": fv("融资利息 -0.17")},
    ])
    resolve_cross_month_statement_context([previous, current])
    assert [row["currency"].value for row in current.sections["other_fund_flows"].rows] == ["HKD", "USD"]


def test_split_security_id_uses_explicit_us_equity_ticker():
    from longbridge_tax_workpaper.cost_basis import canonical_security_id

    text = "2 倍做多黄金 ETF - ProShares UGL US Equity Stock Split Amount: 4 for 1"
    assert canonical_security_id(text) == "US:UGL"
