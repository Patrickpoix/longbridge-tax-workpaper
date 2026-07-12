from __future__ import annotations

from pathlib import Path

import pytest

from longbridge_tax_workpaper.config import prepare_runtime_config, runtime_config_environment
from longbridge_tax_workpaper.cost_basis import build_cost_basis_report
from longbridge_tax_workpaper.schema import FieldValue, SectionResult, StatementResult


def fv(value):
    return FieldValue.native(value, confidence=1.0)


def trade(*, date: str, order: str, side: str, qty: float, amount: float, total: float):
    return {
        "trade_date": fv(date),
        "order_id": fv(order),
        "side": fv(side),
        "direction": fv("买入" if side == "BUY" else "卖出"),
        "symbol": fv("1288 农业银行"),
        "quantity": fv(qty),
        "price": fv(amount / qty),
        "amount": fv(amount),
        "total_amount": fv(total),
        "currency": fv("HKD"),
        "order_time": fv("10:00:00"),
        "execution_time": fv("10:00:01"),
    }


def holding(*, opening: float, ending: float):
    return {
        "name": fv("1288 农业银行"),
        "asset_type": fv("股票"),
        "currency": fv("HKD"),
        "opening_position": fv(opening),
        "ending_position": fv(ending),
        "cost": fv(999.0),  # Broker display cost is deliberately ignored for prior-period reconstruction.
    }


def statement(month: str, *, trades=None, holdings=None) -> StatementResult:
    item = StatementResult(month, f"statement-monthly-{month}-H00000001.pdf")
    item.sections["stock_trades"] = SectionResult("stock_trades", rows=list(trades or []))
    item.sections["option_trades"] = SectionResult("option_trades", rows=[])
    item.sections["holdings"] = SectionResult("holdings", rows=list(holdings or []))
    item.sections["other_fund_flows"] = SectionResult("other_fund_flows", rows=[])
    return item


def test_complete_prior_trade_ledger_builds_opening_lot_and_exact_pnl(tmp_path: Path):
    prior = statement(
        "202412",
        trades=[trade(date="2024.12.10", order="P1", side="BUY", qty=10, amount=100, total=-101)],
        holdings=[holding(opening=0, ending=10)],
    )
    current = statement(
        "202501",
        trades=[
            trade(date="2025.01.05", order="B1", side="BUY", qty=10, amount=200, total=-202),
            trade(date="2025.01.10", order="S1", side="SELL", qty=15, amount=450, total=447),
        ],
        holdings=[holding(opening=10, ending=5)],
    )
    paths = prepare_runtime_config(
        tmp_path / "config",
        tax_year=2025,
        account_opening_month="202412",
        fx_rates={"HKD": 0.9, "USD": 7.0},
    )
    with runtime_config_environment(paths):
        report = build_cost_basis_report([current], [prior])

    assert report["errors"] == []
    assert report["ready"] is True
    opening = report["opening_lots"]
    assert {row["method"] for row in opening} == {"FIFO", "MOVING_AVERAGE"}
    assert all(row["security_id"] == "HK:01288" for row in opening)
    assert all(row["quantity"] == 10 for row in opening)
    assert all(row["total_cost"] == 101 for row in opening)
    assert all(row["evidence_status"] == "verified_from_complete_prior_trade_ledger" for row in opening)
    monthly = report["prior_period_coverage"].get("monthly_reconciliation", [])
    assert report["prior_period_coverage"].get("monthly_reconciliation_status", "not_applicable") in ("ok", "not_applicable")

    fifo = report["fifo"].disposals[0]
    moving = report["moving_average"].disposals[0]
    assert fifo["allocated_cost"] == pytest.approx(202.0)
    assert fifo["realized_pnl"] == pytest.approx(245.0)
    assert fifo["realized_pnl_cny"] == pytest.approx(220.5)
    assert fifo["reference_tax_on_positive_pnl_cny"] == pytest.approx(44.10)
    assert moving["allocated_cost"] == pytest.approx(227.25)
    assert moving["realized_pnl"] == pytest.approx(219.75)
    assert moving["realized_pnl_cny"] == pytest.approx(197.775)
    assert moving["reference_tax_on_positive_pnl_cny"] == pytest.approx(39.56)
    assert report["fifo"].reconciliation[0]["difference"] == 0
    assert report["moving_average"].reconciliation[0]["difference"] == 0


def test_missing_fx_never_becomes_zero_cny(tmp_path: Path):
    prior = statement(
        "202412",
        trades=[trade(date="2024.12.10", order="P1", side="BUY", qty=10, amount=100, total=-101)],
        holdings=[holding(opening=0, ending=10)],
    )
    current = statement(
        "202501",
        trades=[trade(date="2025.01.10", order="S1", side="SELL", qty=5, amount=100, total=99)],
        holdings=[holding(opening=10, ending=5)],
    )
    paths = prepare_runtime_config(
        tmp_path / "config",
        tax_year=2025,
        account_opening_month="202412",
        fx_rates={},
    )
    with runtime_config_environment(paths):
        report = build_cost_basis_report([current], [prior])
    row = report["fifo"].disposals[0]
    assert row["realized_pnl"] == pytest.approx(48.5)
    assert row["realized_pnl_cny"] is None
    assert row["reference_tax_on_positive_pnl_cny"] is None
    assert row["cny_conversion_status"] == "incomplete_missing_fx"


def test_split_cash_compensation_blocks_cost_report(tmp_path: Path):
    current = statement("202501", holdings=[])
    current.sections["other_fund_flows"].rows.append({
        "date": fv("2025.01.15"),
        "raw_detail": fv("UGL Stock Split Amount: 4 for 1 cash in lieu of fractional share"),
        "currency": fv("USD"),
        "cash_amount": fv(0.25),
    })
    paths = prepare_runtime_config(tmp_path / "config", tax_year=2025, account_opening_month="202501", fx_rates={"USD": 7, "HKD": 0.9})
    with runtime_config_environment(paths):
        report = build_cost_basis_report([current], [])
    assert report["ready"] is False
    assert any("split cash/fractional-share compensation" in item for item in report["errors"])


def test_negative_broker_display_cost_is_never_used_as_opening_tax_basis(tmp_path: Path):
    current = statement("202501", holdings=[{
        "name": fv("PDD 拼多多"),
        "asset_type": fv("股票"),
        "currency": fv("USD"),
        "opening_position": fv(1),
        "ending_position": fv(1),
        "cost": fv(-485.50),
    }])
    paths = prepare_runtime_config(
        tmp_path / "config",
        tax_year=2025,
        account_opening_month="202501",
        fx_rates={"USD": 7.0, "HKD": 0.9},
    )
    with runtime_config_environment(paths):
        report = build_cost_basis_report([current], [])
    assert report["opening_lots"] == []
    assert report["ready"] is False
    assert any("non-positive" in error for error in report["errors"])
