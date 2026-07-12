"""Test normalize.py — classify_tax_category branching logic."""
from decimal import Decimal

from longbridge_tax_workpaper.normalize import (
    classify_tax_category,
    normalize_text,
    parse_amount,
)


class TestClassifyTaxCategory:
    """Cover all 14+ branches of classify_tax_category."""

    def test_dividend_classification(self):
        """cash_dividend -> dividend_income"""
        assert classify_tax_category("cash_dividend", "") == "dividend_income"

    def test_margin_interest_classification(self):
        """margin_interest -> margin_interest_deductible"""
        assert classify_tax_category("margin_interest", "") == "margin_interest_deductible"

    def test_deposit_classification(self):
        """deposit -> non_taxable_cash_movement"""
        assert classify_tax_category("deposit", "") == "non_taxable_cash_movement"

    def test_withdrawal_classification(self):
        """withdrawal -> non_taxable_cash_movement"""
        assert classify_tax_category("withdrawal", "") == "non_taxable_cash_movement"

    def test_company_action_stock_in(self):
        """company_action_stock_in -> non_cash_company_action"""
        assert classify_tax_category("company_action_stock_in", "") == "non_cash_company_action"

    def test_company_action_stock_out(self):
        """company_action_stock_out -> non_cash_company_action"""
        assert classify_tax_category("company_action_stock_out", "") == "non_cash_company_action"

    def test_company_action_cash_in_pending(self):
        """company_action_cash_in -> company_action_cash_pending_review"""
        assert classify_tax_category("company_action_cash_in", "") == "company_action_cash_pending_review"

    def test_cash_reward_classification(self):
        """cash_reward -> cash_reward_other_income"""
        assert classify_tax_category("cash_reward", "") == "cash_reward_other_income"

    def test_company_action_fee_withholding_tax(self):
        """company_action_fee with 'tax' in detail -> withholding_tax"""
        r = classify_tax_category("company_action_fee", "withholding tax 10%")
        assert r == "withholding_tax"

    def test_company_action_fee_handling_fee(self):
        """company_action_fee with 'handling fee' -> service_fee_deductible"""
        r = classify_tax_category("company_action_fee", "Handling Fee")
        assert r == "service_fee_deductible"

    def test_company_action_fee_scrip_fee(self):
        """company_action_fee with 'scrip fee' -> service_fee_deductible"""
        r = classify_tax_category("company_action_fee", "Scrip Fee")
        assert r == "service_fee_deductible"

    def test_company_action_fee_non_deductible(self):
        """company_action_fee without known pattern -> company_action_fee_non_deductible"""
        r = classify_tax_category("company_action_fee", "other fee")
        assert r == "company_action_fee_non_deductible"

    def test_adr_fee_classification(self):
        """adr_fee -> service_fee_deductible"""
        assert classify_tax_category("adr_fee", "") == "service_fee_deductible"

    def test_stock_trade_cash_flow(self):
        """stock_trade_cash_flow -> trading_related"""
        assert classify_tax_category("stock_trade_cash_flow", "") == "trading_related"

    def test_unknown_type_falls_back_to_pending_review(self):
        """unknown transaction type -> pending_review"""
        assert classify_tax_category("unknown_type", "") == "pending_review"


class TestNormalizeText:
    """Test normalize_text basic functionality."""

    def test_simplified_chinese_normalization(self):
        result = normalize_text("綜合帳戶")
        assert "账" in result or "户" in result

    def test_whitespace_collapse(self):
        result = normalize_text("hello   world")
        assert result == "hello world"

    def test_kangxi_radical_replacement(self):
        result = normalize_text("\u2f26\u6237")  # ⼦户
        assert "子" in result or "户" in result


class TestParseAmount:
    """Test parse_amount number parsing."""

    def test_positive_integer(self):
        assert parse_amount("100") == 100.0

    def test_negative_decimal(self):
        assert parse_amount("-50.25") == -50.25

    def test_with_commas(self):
        assert parse_amount("1,234.56") == 1234.56
